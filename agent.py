"""Orchestration: a state machine over one conversation.

The agent's whole job is sequencing: extract slots, consult tools for facts,
ask policy for verdicts, emit envelopes, and hand structured events to the
provider for narration. It computes no verdict itself. Memory is three tiers
with three lifetimes: turn slots (this function call), conversation state
(this object), and customer records (the store). Collapsing them breaks the
second request of a conversation — an earlier turn's slot must not survive
into this turn's decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import envelope
import policy
import tools
from llm import (
    ExtractionContext,
    NarrationEvent,
    PendingQuestion,
    Provider,
    Request,
)
from recorder import NULL_RECORDER, Recorder
from store import (
    CURRENT_CUSTOMER_ID,
    GENERIC_TITLE_WORDS,
    SERVICE_LEVELS,
    TODAY,
    Order,
)

# What one emitted action looks like to a caller: the envelope, and how
# delivery went.
Emission = Tuple[dict, str]


@dataclass
class TurnResult:
    reply: str
    envelopes: List[Emission]


@dataclass
class _Turn:
    """Turn memory: lives for exactly one handle_turn call."""

    raw_text: str
    replies: List[str] = field(default_factory=list)
    envelopes: List[Emission] = field(default_factory=list)
    return_handled: bool = False
    # Orders already decided this turn: one decision per order per turn.
    finished_orders: Set[str] = field(default_factory=set)
    # A clarifying question is a turn-level outcome, not a per-request one:
    # it is asked at most once, and only if no return completed this turn.
    clarify_candidates: Optional[List[Order]] = None


class Agent:
    def __init__(
        self,
        provider: Provider,
        conversation_id: str,
        customer_id: str = CURRENT_CUSTOMER_ID,
        recorder: Recorder = NULL_RECORDER,
    ) -> None:
        self.provider = provider
        self.conversation_id = conversation_id
        # A bystander that does nothing unless someone is watching. Nothing
        # below ever asks which recorder this is, which is why handing one in
        # cannot change an outcome.
        self.recorder = recorder
        # Customer memory: durable identity; records live in the store.
        self.customer_id = customer_id
        # Conversation memory: survives across turns, dies with the session.
        self.pending: Optional[PendingQuestion] = None
        self.clarify_attempts = 0
        self.focus_order_id: Optional[str] = None
        # How many times each order's return has already been denied. A
        # repeat is a dispute, which policy escalates.
        self.denials: Dict[str, int] = {}

    # -- the single entry point -------------------------------------------

    def handle_turn(self, text: str) -> TurnResult:
        turn = _Turn(raw_text=text)
        requests = self.provider.extract(text, self._context())
        # The slots, verbatim. There is no amount field for an injected
        # instruction to land in, and this note is where that is visible.
        self.recorder.note(
            "extract",
            {"provider": self.provider.name, "requests": list(requests)},
        )
        if self.pending is not None:
            self._continue_or_switch(requests, turn)
        else:
            self.recorder.note(
                "route",
                {
                    "branch": "no_pending_question",
                    "intents": [r.intent for r in requests],
                },
            )
            self._process(requests, turn)
        self._ask_deferred_question(turn)
        if not turn.replies:
            self._narrate("help", {}, turn)
        return TurnResult(" ".join(turn.replies), turn.envelopes)

    # -- the intent-switching precedence rule -----------------------------

    def _continue_or_switch(self, requests: List[Request], turn: _Turn) -> None:
        """A turn that answers the asked-about slot is a continuation. A turn
        that answers nothing and names a different intent is a topic change.
        A turn that does neither gets bounded re-asks, then a human."""
        answers = [r for r in requests if _is_answer(r)]
        asked_about = list(self.pending.option_ids)
        if answers:
            others = [r for r in requests if not _is_answer(r)]
            self._note_route("continuation", asked_about, answers)
            self._continue_procedure(answers, others, turn)
        elif any(r.intent for r in requests):
            self._note_route("topic_change", asked_about, requests)
            self._switch_topic(requests, turn)
        else:
            self._note_route("reask", asked_about, requests)
            self._reask_or_escalate(turn)

    def _note_route(
        self, branch: str, asked_about: List[str], requests: List[Request]
    ) -> None:
        self.recorder.note(
            "route",
            {
                "branch": branch,
                "pending_question": self.pending.kind if self.pending else None,
                "asked_about": asked_about,
                "intents": [r.intent for r in requests],
            },
        )

    def _continue_procedure(
        self, answers: List[Request], others: List[Request], turn: _Turn
    ) -> None:
        order, used = self._order_from_answer(answers)
        if order is None:
            # The answer engaged the question but resolved nothing (an
            # out-of-range number, an unknown id). That is a failed
            # clarification attempt — counted, so the loop stays bounded.
            self._reask_or_escalate(turn)
            self._process(others, turn)
            return
        self.pending = None
        self.clarify_attempts = 0
        self._finish_return(order, turn)
        # One request answered the question; the rest of the turn still
        # deserves handling ("1 and also return BK-0987").
        leftover = [r for r in answers if r is not used]
        self._process(leftover + others, turn)

    def _switch_topic(self, requests: List[Request], turn: _Turn) -> None:
        # Abandoning a half-filled procedure beats trapping the customer in
        # it; restating the intent later simply starts it again.
        self.pending = None
        self.clarify_attempts = 0
        self._process(requests, turn)
        # Say so, unless this turn already started or finished a return —
        # then there is nothing set aside to mention.
        if turn.clarify_candidates is None and not turn.return_handled:
            self._narrate("return_parked", {}, turn)

    def _reask_or_escalate(self, turn: _Turn) -> None:
        self.clarify_attempts += 1
        spent = policy.clarify_limit_reached(self.clarify_attempts)
        self.recorder.note(
            "clarify",
            {
                "step": "limit_reached" if spent else "reask",
                "attempts": self.clarify_attempts,
                "limit": policy.MAX_CLARIFY_ATTEMPTS,
                "constant": "MAX_CLARIFY_ATTEMPTS",
            },
        )
        if spent:
            self._emit_escalation(policy.ESCALATED_CLARIFY_LIMIT, None, turn)
            self.pending = None
            self.clarify_attempts = 0
            return
        options = [tools.get_order(oid) for oid in self.pending.option_ids]
        self._narrate(
            "reask_which_order", {"options": _option_facts(options)}, turn
        )

    # -- request dispatch --------------------------------------------------

    def _process(self, requests: List[Request], turn: _Turn) -> None:
        for request in requests:
            if request.intent == "order_status":
                self._answer_status(request, turn)
            elif request.intent == "return_request":
                self._handle_return(request, turn)
            elif request.intent == "policy_question":
                self._answer_policy(request, turn)
            elif request.intent == "human_handoff":
                self._emit_escalation(
                    policy.ESCALATED_CUSTOMER_REQUEST,
                    self.focus_order_id,
                    turn,
                )

    # -- reads: order status ----------------------------------------------

    def _answer_status(self, request: Request, turn: _Turn) -> None:
        order, rule = self._resolve_read_target(request)
        if order is None:
            self._narrate("order_not_found", {}, turn)
            return
        self.focus_order_id = order.order_id
        facts = _order_facts(order)
        # Whether the customer already knows which order this is. A follow-up
        # about the order under discussion has the same answer as the question
        # before it, and often should — but saying the identical sentence back
        # reads as not having listened. Wording only; no verdict reads it.
        facts["already_discussed"] = rule == "order_under_discussion"
        self._narrate("status_report", facts, turn)

    def _resolve_read_target(
        self, request: Request
    ) -> Tuple[Optional[Order], str]:
        """Reads are cheap and self-describing (the reply names the order),
        so a wrong guess costs little: prefer an explicit reference, then
        the order under discussion, then the one still in transit.

        Each way out records which rule got there, because "it used the order
        already under discussion" and "it fell back to the most recent one"
        look identical in the reply and are very different facts.
        """
        if request.order_id:
            order = tools.get_order(request.order_id)
            visible = policy.can_view(order, self.customer_id)
            return self._note_read(
                order if visible else None, "explicit_reference"
            )
        by_title = self._orders_by_title_words(request.title_words)
        if len(by_title) == 1:
            return self._note_read(by_title[0], "unique_title_match")
        if self.focus_order_id:
            return self._note_read(
                tools.get_order(self.focus_order_id), "order_under_discussion"
            )
        in_transit = tools.in_transit_orders(self.customer_id)
        if len(in_transit) == 1:
            return self._note_read(in_transit[0], "only_one_in_transit")
        return self._note_read(
            _most_recent(tools.orders_for_customer(self.customer_id)),
            "most_recently_ordered",
        )

    def _note_read(
        self, order: Optional[Order], rule: str
    ) -> Tuple[Optional[Order], str]:
        self.recorder.note(
            "lookup",
            {
                "kind": "order_read",
                "rule": rule,
                "order_id": order.order_id if order else None,
                "title": order.title if order else None,
                "status": order.status if order else None,
            },
        )
        return order, rule

    # -- writes: the return procedure --------------------------------------

    def _handle_return(self, request: Request, turn: _Turn) -> None:
        # A second unreferenced return request in the same turn is the same
        # ask restated, not a new procedure.
        if turn.return_handled and not _fills_which_order(request):
            return
        if request.order_id:
            order = tools.get_order(request.order_id)
            self.recorder.note(
                "lookup",
                {
                    "kind": "return_target",
                    "rule": "explicit_reference",
                    "asked_for": request.order_id,
                    "order_id": order.order_id if order else None,
                    "title": order.title if order else None,
                },
            )
            self._finish_return(order, turn)
            return
        by_title = self._orders_by_title_words(request.title_words)
        if request.title_words:
            self.recorder.note(
                "lookup",
                {
                    "kind": "return_target",
                    "rule": "title_match",
                    "title_words": list(request.title_words),
                    "matched_ids": [o.order_id for o in by_title],
                    "note": "a write never falls back: no match asks, and "
                            "more than one asks",
                },
            )
        # A title only counts as a reference if the customer actually named
        # the book. Ordinary words that happen to sit inside a title —
        # "book", "cover", "light" — neither identify one nor nominate it as
        # a candidate: "the Escher book" names Godel, Escher, Bach, and must
        # not drag in every title containing the word "book".
        named = [o for o in by_title if self._names_the_order(o, request)]
        if len(named) == 1:
            self._finish_return(named[0], turn)
        elif len(named) > 1:
            self._defer_which_order(named, turn)
        else:
            # Nothing matched, or everything that matched was coincidence.
            # Both end in the same place: ask the question we would have asked
            # if they had said nothing, because that is how much they said.
            self._start_return(turn)

    def _start_return(self, turn: _Turn) -> None:
        # An unreferenced return request ("refund it anyway") continues the
        # return already under dispute, if there is one. Otherwise it is a
        # fresh choice among what could actually be returned.
        if self.focus_order_id and self.denials.get(self.focus_order_id):
            self._finish_return(tools.get_order(self.focus_order_id), turn)
            return
        # What the agent volunteers is what policy would actually grant. A
        # customer with thirty-four delivered books does not want thirty-four
        # of them read back, and being offered a book only to be refused it
        # costs them a turn to be told no.
        delivered = tools.delivered_orders(self.customer_id)
        candidates = policy.returnable_now(delivered, self.customer_id, TODAY)
        needs_choice = policy.should_clarify(len(candidates))
        self.recorder.note(
            "candidates",
            {
                "source": "returnable_now",
                "delivered_count": len(delivered),
                "candidate_ids": [o.order_id for o in candidates],
                "count": len(candidates),
                "should_clarify": needs_choice,
                "rule": "offer what policy would approve, then ask only when "
                        "more than one of those could take the write",
            },
        )
        if not candidates:
            self._narrate(
                "no_returnable_orders",
                {
                    "delivered_count": len(delivered),
                    "window_days": policy.RETURN_WINDOW_DAYS,
                },
                turn,
            )
        elif needs_choice:
            self._defer_which_order(candidates, turn)
        else:
            self._finish_return(candidates[0], turn)

    def _defer_which_order(
        self, candidates: List[Order], turn: _Turn
    ) -> None:
        """Record that the turn needs a clarifying question. Asking waits
        until the whole turn is read, because a later request may answer it."""
        turn.clarify_candidates = candidates
        self.recorder.note(
            "clarify",
            {
                "step": "deferred",
                "option_ids": [o.order_id for o in candidates],
                "why": "a later request in the same turn may answer it",
            },
        )

    def _ask_deferred_question(self, turn: _Turn) -> None:
        """Ask the deferred clarifying question — unless a later request in
        the same turn already completed a return, in which case the vague ask
        was the same request restated and the question would be stale."""
        if turn.clarify_candidates is None or turn.return_handled:
            return
        self.pending = PendingQuestion(
            kind="which_order",
            option_ids=tuple(o.order_id for o in turn.clarify_candidates),
        )
        self.clarify_attempts = 0
        # Deciding to ask is this note. Phrasing the question is the narrate
        # note that follows — the two sit on opposite sides of the boundary
        # and must never render as one row.
        self.recorder.note(
            "clarify",
            {
                "step": "asked",
                "question": self.pending.kind,
                "option_ids": list(self.pending.option_ids),
                "attempts": self.clarify_attempts,
                "limit": policy.MAX_CLARIFY_ATTEMPTS,
            },
        )
        self._narrate(
            "clarify_which_order",
            {"options": _option_facts(turn.clarify_candidates)},
            turn,
        )

    def _finish_return(self, order: Optional[Order], turn: _Turn) -> None:
        if order is not None:
            if order.order_id in turn.finished_orders:
                return  # one decision per order per turn, however often named
            turn.finished_orders.add(order.order_id)
        turn.return_handled = True
        verdict = policy.decide_return(order, self.customer_id, TODAY)
        prior_denials = (
            self.denials.get(verdict.order_id, 0) if verdict.order_id else 0
        )
        if verdict.order_id:
            verdict = policy.escalate_if_disputed(verdict, prior_denials)
        # The whole answer, and the named constants it rests on, so anything
        # on screen can be walked back to the line of policy that produced it.
        self.recorder.note(
            "verdict",
            {
                "decision": verdict.decision,
                "reason_code": verdict.reason_code,
                "order_id": verdict.order_id,
                "refund_amount": verdict.refund_amount,
                "prior_denials": prior_denials,
                "today": TODAY,
                "constants": policy.constants_for(verdict.reason_code),
            },
        )
        if verdict.decision == "approve_refund":
            self._emit_refund(verdict, order, turn)
        elif verdict.decision == "deny":
            already = self.denials.get(order.order_id, 0)
            self.denials[order.order_id] = already + 1
            self.focus_order_id = order.order_id
            self._narrate("return_denied", _denial_facts(verdict, order), turn)
        elif verdict.decision == "escalate":
            self._emit_escalation(verdict.reason_code, verdict.order_id, turn)
        else:
            self._narrate("order_not_found", {}, turn)

    def _emit_refund(
        self, verdict: policy.Verdict, order: Order, turn: _Turn
    ) -> None:
        emitted = envelope.emit(
            action="refund",
            conversation_id=self.conversation_id,
            order_id=order.order_id,
            reason_code=verdict.reason_code,
            amount=verdict.refund_amount,
            customer_note=turn.raw_text,
        )
        turn.envelopes.append(emitted)
        self._note_envelope(emitted)
        self.focus_order_id = order.order_id
        facts = _order_facts(order)
        facts["amount"] = verdict.refund_amount
        facts["window_days"] = policy.RETURN_WINDOW_DAYS
        # When the money lands is a published commitment, looked up from the
        # profile's service levels — not a number a template asserts. That is
        # what lets a hosted narrator, forbidden from inventing timeframes,
        # state the same one: it is stating a fact it was handed.
        facts["posting_target"] = SERVICE_LEVELS.get("refund_posting")
        self._narrate("refund_approved", facts, turn)

    def _emit_escalation(
        self, reason_code: str, order_id: Optional[str], turn: _Turn
    ) -> None:
        emitted = envelope.emit(
            action="escalate_to_human",
            conversation_id=self.conversation_id,
            order_id=order_id,
            reason_code=reason_code,
            customer_note=turn.raw_text,
        )
        turn.envelopes.append(emitted)
        self._note_envelope(emitted)
        # The published response time travels as a fact on the event, not as
        # a string in a template, so a hosted narrator — whose prompt forbids
        # inventing promises — is able to state it too. It is a commitment
        # from the profile, not a threshold any decision reads.
        self._narrate(
            "escalation",
            {
                "reason_code": reason_code,
                "response_target": SERVICE_LEVELS.get(
                    "escalation_first_response"
                ),
            },
            turn,
        )

    def _note_envelope(self, emitted: Emission) -> None:
        """The action record and how delivery went. The audit line was
        already written before the hop, so a failed delivery here has still
        lost nothing but the delivery."""
        envelope_record, delivery = emitted
        self.recorder.note(
            "envelope",
            {"envelope": envelope_record, "delivery": delivery},
        )

    # -- reads: policy questions -------------------------------------------

    def _answer_policy(self, request: Request, turn: _Turn) -> None:
        article = tools.search_policy(request.text)
        self.recorder.note(
            "lookup",
            {
                "kind": "policy_article",
                "query": request.text,
                "article_id": article.article_id if article else None,
                "title": article.title if article else None,
                "floor": tools.MIN_KEYWORD_MATCHES,
                "rule": "below the keyword floor, or a tie for best, returns "
                        "nothing rather than the nearest article",
            },
        )
        if article is None:
            self._narrate("kb_miss", {}, turn)
        else:
            self._narrate("kb_answer", {"summary": article.summary}, turn)

    # -- shared helpers ----------------------------------------------------

    def _context(self) -> ExtractionContext:
        titles = tuple(
            o.title for o in tools.orders_for_customer(self.customer_id)
        )
        return ExtractionContext(known_titles=titles, pending=self.pending)

    def _orders_by_title_words(
        self, title_words: Tuple[str, ...]
    ) -> List[Order]:
        matches = []
        for word in title_words:
            for order in tools.find_orders_by_title_word(
                self.customer_id, word
            ):
                if order not in matches:
                    matches.append(order)
        return matches

    def _order_from_answer(
        self, requests: List[Request]
    ) -> Tuple[Optional[Order], Optional[Request]]:
        """Resolve the customer's answer to "which order?" and report which
        request resolved it. (None, None) means nothing resolved a unique
        order — a failed clarification attempt."""
        for request in requests:
            if request.order_id:
                return tools.get_order(request.order_id), request
            if request.option_number is not None:
                index = request.option_number - 1
                if 0 <= index < len(self.pending.option_ids):
                    order_id = self.pending.option_ids[index]
                    return tools.get_order(order_id), request
            if request.title_words:
                matches = self._orders_by_title_words(request.title_words)
                # The same bar as an unprompted return. Answering "which
                # book?" with a word that only coincidentally sits in a title
                # has not answered it, and counts as a failed attempt rather
                # than a resolution.
                named = [
                    o for o in matches if self._names_the_order(o, request)
                ]
                if len(named) == 1:
                    return named[0], request
        return None, None

    def _names_the_order(self, order: Order, request: Request) -> bool:
        """Did the customer name this book, or merely use words that are in
        its title?

        Which words are generic is catalog data; how many are enough is
        policy. Neither judgement is made here — this counts, and records the
        count, so a weak reference is visible in the trace rather than being a
        thing that silently did not happen.
        """
        words = {word.lower() for word in request.title_words}
        in_title = words & tools.title_tokens(order.title)
        distinctive = in_title - GENERIC_TITLE_WORDS
        strong = policy.title_reference_is_strong(
            len(in_title), len(distinctive)
        )
        self.recorder.note(
            "candidates",
            {
                "source": "title_reference",
                "order_id": order.order_id,
                "title": order.title,
                "matched_words": sorted(in_title),
                "distinctive_words": sorted(distinctive),
                "strong_enough_to_act": strong,
                "constant": "MIN_TITLE_WORDS_FOR_WRITE",
                "limit": policy.MIN_TITLE_WORDS_FOR_WRITE,
                "rule": "a write acts on a title only when the customer named "
                        "it; generic words never identify one",
            },
        )
        return strong

    def _narrate(self, kind: str, facts: dict, turn: _Turn) -> None:
        text = self.provider.narrate(NarrationEvent(kind=kind, facts=facts))
        # A sentence repeated inside one reply is never a feature.
        duplicate = text in turn.replies
        self.recorder.note(
            "narrate",
            {
                "provider": self.provider.name,
                "event": kind,
                "facts": facts,
                "text": text,
                "suppressed_duplicate": duplicate,
            },
        )
        if not duplicate:
            turn.replies.append(text)


# -- module helpers ---------------------------------------------------------


def _fills_which_order(request: Request) -> bool:
    return bool(
        request.order_id
        or request.title_words
        or request.option_number is not None
    )


def _is_answer(request: Request) -> bool:
    """A request answers "which order?" only if it picks something AND does
    not carry its own different ask. Asking about an order is not choosing
    it — "where's my Dune order?" must never spend a clarification slot."""
    return _fills_which_order(request) and request.intent in (
        None,
        "return_request",
    )


def _order_facts(order: Order) -> dict:
    return {
        "order_id": order.order_id,
        "title": order.title,
        "status": order.status,
        "delivered_on": (
            order.delivered_on.isoformat() if order.delivered_on else None
        ),
        "returned_on": (
            order.returned_on.isoformat() if order.returned_on else None
        ),
        "eta": order.eta.isoformat() if order.eta else None,
        "carrier": order.carrier,
    }


def _denial_facts(verdict: policy.Verdict, order: Order) -> dict:
    facts = _order_facts(order)
    facts["reason_code"] = verdict.reason_code
    facts["window_days"] = policy.RETURN_WINDOW_DAYS
    return facts


def _option_facts(orders: List[Order]) -> List[dict]:
    return [{"order_id": o.order_id, "title": o.title} for o in orders if o]


def _most_recent(orders: List[Order]) -> Optional[Order]:
    if not orders:
        return None
    return max(orders, key=lambda o: o.ordered_on)
