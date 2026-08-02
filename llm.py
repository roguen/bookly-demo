"""The language model boundary. The model has exactly two jobs.

Extraction: one customer turn in, structured slots out.
Narration: one structured event in — a decision or a fact — English out.

Nothing else crosses this boundary. No verdict, amount, or reason code is
ever produced here — those come from policy.py, which this module never
imports. The default provider is a rules-based stand-in so the demo runs
with no dependencies and no API key; because both jobs are narrow and
structured, the stand-in is serviceable. Setting a vendor key swaps in a
hosted model — Anthropic or OpenAI — without changing any other file. The
two hosted providers differ by one method: the network call.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Tuple

# Voice only: who the agent says it is, and how it declines. This is the one
# thing this module reads from the profile, and it is deliberately not
# behaviour — no verdict, amount, or reason code is affected by any of it,
# and policy.py remains un-imported here. Both providers read the same values,
# so the stand-in and a hosted model decline in the same words.
from store import AGENT, BRAND

# ---------------------------------------------------------------------------
# The data that crosses the boundary.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingQuestion:
    """A clarifying question the agent is waiting on."""

    kind: str  # only "which_order" today
    option_ids: Tuple[str, ...]


@dataclass(frozen=True)
class ExtractionContext:
    """What the extractor may know: this customer's titles, nothing more."""

    known_titles: Tuple[str, ...]
    pending: Optional[PendingQuestion] = None


@dataclass(frozen=True)
class Request:
    """One thing the customer asked for in a turn. A turn can hold several."""

    intent: Optional[str]  # one of VALID_INTENTS (below), or None
    order_id: Optional[str] = None
    title_words: Tuple[str, ...] = ()
    option_number: Optional[int] = None
    text: str = ""


@dataclass(frozen=True)
class NarrationEvent:
    """A structured fact bundle for the model to phrase. Facts only."""

    kind: str
    facts: dict = field(default_factory=dict)


VALID_INTENTS = frozenset(
    [
        "order_status",
        "order_history",
        "refund_status",
        "agent_identity",
        "return_request",
        "policy_question",
        "human_handoff",
        # The door for "none of the above": a request this support does not
        # cover. It exists so a hosted model can say "I can't help with that"
        # instead of forcing an unmodeled question onto the nearest intent.
        "out_of_scope",
    ]
)


class Provider(Protocol):
    """The whole contract between the agent and a language model: a name for
    the transcript, and the two jobs. Both providers below satisfy it, which
    is why swapping them touches no other file."""

    name: str

    def extract(self, text: str, context: ExtractionContext) -> List[Request]:
        ...

    def narrate(self, event: NarrationEvent) -> str:
        ...

# ---------------------------------------------------------------------------
# Rules-based stand-in provider.
# ---------------------------------------------------------------------------

# A turn splits into requests at conjunctions and sentence breaks, so each
# request carries its own slots ("where's my Dune order AND I want to
# return the Escher book" becomes two requests). Deliberately does not split
# on "?" or ",": both appear mid-request often enough that splitting there
# fragments one ask into several. The cost is that two questions joined by a
# question mark arrive as one request — a stand-in limit, not a design one.
SEGMENT_SPLIT_RE = re.compile(r"\s+\band\b\s+|;\s*|\.\s+")

# Catches order ids and nothing else. The format is fixed by the store.
ORDER_ID_RE = re.compile(r"\bBK-\d{4}\b", re.IGNORECASE)

# Catches asking *about* a refund rather than asking for one. Checked before
# RETURN_REQUEST_RE, because "when will the refund show up?" contains the word
# "refund" and is emphatically not a request to start another one — which is
# exactly what it used to be read as, moments after the agent had issued the
# refund being asked about.
REFUND_STATUS_RE = re.compile(
    r"\b(when (?:will|does|do|is|should)|where(?:'s| is)?|how long"
    r"|has|have|did|is)\b[^.?!]{0,40}?\brefunds?\b"
    r"|\brefunds?\b[^.?!]{0,40}?\b(show up|come through|go through|arrive"
    r"|land|post|posted|take|taken|processed|issued yet)\b"
    r"|\bmoney back\b[^.?!]{0,30}?\b(when|yet|arrive|show)\b",
    re.IGNORECASE,
)

# Catches asking to send something back. Deliberately does not catch
# "return policy" / "refund policy" — those are questions, not requests.
RETURN_REQUEST_RE = re.compile(
    r"\b(return|refund|send (?:it |them )?back|money back)\b(?!\s+polic)",
    re.IGNORECASE,
)

# Order status needs a question word AND something being asked about, so
# "how long does shipping take" — a signal with no object — stays a policy
# question rather than becoming a lookup of some arbitrary order.
STATUS_SIGNAL_RE = re.compile(
    r"\b(where|status|track|tracking|when|arrive|arriving|arrived|eta)\b",
    re.IGNORECASE,
)
STATUS_OBJECT_RE = re.compile(
    r"\b(order|package|delivery|it|book|copy)\b", re.IGNORECASE
)

# Catches a question about the account as a whole rather than one order —
# "what have I ordered", "how many books". This needed an intent of its own
# rather than a knowledge-base article: with no home, a hosted model maps the
# question onto order_status, the read falls back to the likeliest single
# order, and the customer gets a fluent answer to a question they did not ask.
# Deliberately checked before order_status, because "what have I ordered" and
# "where is my order" share vocabulary and only one of them is about one book.
ORDER_HISTORY_RE = re.compile(
    r"\b(how many|order history|purchase history|total number"
    r"|(?:all|every|each) (?:of )?(?:my|the) (?:orders|books|purchases)"
    r"|(?:list|show me|see) (?:all )?(?:my|the) (?:orders|books|purchases)"
    r"|(?:everything|all) (?:i|we)(?:'ve| have)? (?:ever )?"
    r"(?:ordered|bought|purchased)"
    r"|what (?:\w+ ){0,3}have i (?:ever )?(?:ordered|bought|purchased))\b",
    re.IGNORECASE,
)

# Catches being asked who the agent is. Its own intent for the same reason:
# routing it through retrieval would need "you" and "your" as article
# keywords, and that makes "how long do you keep your records?" retrieve the
# identity article — the confident-wrong-article failure the floor exists to
# prevent. An honest answer to "what is your name" is not worth reopening it.
IDENTITY_RE = re.compile(
    r"\b(what(?:'s| is| was)? your name|who are you|who am i (?:talking|"
    r"speaking|chatting) (?:to|with)|are you (?:a |an )?"
    r"(?:bot|robot|human|person|real|machine|ai)|your name)\b",
    re.IGNORECASE,
)

# Catches general questions the knowledge base might answer. Retrieval makes
# the real relevance call; this only routes the segment.
#
# The second group is about the agent's own behaviour — "what do you mean by
# limit", "how long until someone gets back to me". An agent that escalates
# because it hit a limit and then cannot explain what the limit was is worse
# than one that never mentions it, so those questions route to retrieval like
# any other. The knowledge base decides whether it can actually answer, and
# still fails closed when it cannot.
POLICY_QUESTION_RE = re.compile(
    r"\b(policy|policies|shipping|password|how long|business days"
    r"|limit|limits|clarif\w*|what do you mean|escalat\w+|sla"
    r"|get back to me|hear back|how soon)\b",
    re.IGNORECASE,
)

# Catches an explicit ask for a person. Kept narrow: it must not fire on
# ordinary mentions of people, only on asking to be handed to one.
HUMAN_HANDOFF_RE = re.compile(
    r"\b(manager|supervisor|representative|escalate"
    r"|(?:speak|talk) to (?:a |an )?(?:person|human|someone|somebody|agent))\b",
    re.IGNORECASE,
)

# The door for "none of the above". Checked last, only after every handled
# intent has been tried, so the listed intents always win. A segment that
# reaches here matched nothing this support does. If it still reads as a request
# or a question — it ends with a question mark, or opens with an interrogative
# or a request verb — the customer asked for something out of scope, and the
# honest move is to say so and offer a person rather than force it onto the
# nearest intent. Pure pleasantries ("hello", "thanks", "that sounds great")
# carry no such signal and fall to the friendly opener instead. This detects
# that a turn is an unhandled request; it does not enumerate the topics that are
# out of scope, because the whole point is not to add intents one at a time.
OUT_OF_SCOPE_RE = re.compile(
    r"\?\s*$"
    r"|^\s*(?:do|does|did|can|could|would|will|how|what|where|when|why|who"
    r"|is|are|should|cancel|change|update|reset|subscribe|unsubscribe"
    r"|buy|sell|recommend|suggest)\b",
    re.IGNORECASE,
)

# Catches a digit that is the entire reply — anchored at both ends, because
# a stray "2" inside a sentence is a quantity, not a choice.
OPTION_DIGIT_RE = re.compile(r"^\s*(?:option\s+)?([1-9])\s*[.)]?\s*$")

# Ordinals are unambiguous enough to catch anywhere in a reply ("the first
# one, please"), unlike bare digits. Deferrals are screened out first.
ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3}
ORDINAL_RE = re.compile(r"\b(first|second|third)\b", re.IGNORECASE)

# Cardinal words ("one") count as answers only in replies short enough to BE
# an answer; inside a longer sentence "one" is usually just a word.
CARDINAL_WORDS = {"one": 1, "two": 2, "three": 3}
CARDINAL_RE = re.compile(r"\b(one|two|three)\b", re.IGNORECASE)
CARDINAL_MAX_WORDS = 3

# A number word inside a delegating reply ("just pick one", "either one",
# "pick the first one") is not a choice — the customer is handing the choice
# back. Ambiguity on a write path always breaks toward asking again.
DEFERRAL_RE = re.compile(
    r"\b(pick|choose|decide|either|any|whatever|whichever"
    r"|don'?t care|up to you)\b",
    re.IGNORECASE,
)

# Title words shorter than this are too common to identify a book.
MIN_TITLE_WORD_LEN = 4

STOPWORDS = frozenset(["the", "this", "that", "with", "from", "and"])


class RulesProvider:
    """Regex extraction and template narration. No network, no state."""

    name = "rules-based stand-in"

    def extract(self, text: str, context: ExtractionContext) -> List[Request]:
        segments = [s for s in SEGMENT_SPLIT_RE.split(text) if s and s.strip()]
        return [self._extract_segment(s, context) for s in segments]

    def _extract_segment(
        self, segment: str, context: ExtractionContext
    ) -> Request:
        id_match = ORDER_ID_RE.search(segment)
        title_words = self._title_words(segment, context.known_titles)
        has_reference = bool(id_match or title_words)
        return Request(
            intent=self._intent_of(segment, has_reference),
            order_id=id_match.group(0).upper() if id_match else None,
            title_words=title_words,
            option_number=self._option_number(segment),
            text=segment.strip(),
        )

    def _intent_of(self, segment: str, has_reference: bool) -> Optional[str]:
        # Asking *about* a refund beats asking to do something, and has to be
        # tested first: every phrasing of it contains the word "refund".
        if REFUND_STATUS_RE.search(segment):
            return "refund_status"
        # Asking to do something beats asking about something: a segment that
        # requests a return wins even if it also mentions the policy.
        if RETURN_REQUEST_RE.search(segment):
            return "return_request"
        if HUMAN_HANDOFF_RE.search(segment):
            return "human_handoff"
        if IDENTITY_RE.search(segment):
            return "agent_identity"
        # Before order_status: "what have I ordered" and "where is my order"
        # share vocabulary, and only one of them is about a single book.
        if ORDER_HISTORY_RE.search(segment):
            return "order_history"
        # An explicit order reference — an id or a title — counts as the
        # object: "what's the status of Dune" is a status question even
        # without the word "order".
        if STATUS_SIGNAL_RE.search(segment) and (
            STATUS_OBJECT_RE.search(segment) or has_reference
        ):
            return "order_status"
        if POLICY_QUESTION_RE.search(segment):
            return "policy_question"
        # Last, and only after every handled intent has failed: a request the
        # agent cannot cover reads as out of scope; a pleasantry reads as
        # nothing, and gets the friendly opener.
        if OUT_OF_SCOPE_RE.search(segment):
            return "out_of_scope"
        return None

    def _title_words(
        self, segment: str, known_titles: Tuple[str, ...]
    ) -> Tuple[str, ...]:
        lowered = segment.lower()
        hits = []
        for title in known_titles:
            for word in re.findall(r"[a-z]+", title.lower()):
                if len(word) < MIN_TITLE_WORD_LEN or word in STOPWORDS:
                    continue
                if re.search(r"\b%s\b" % re.escape(word), lowered):
                    hits.append(word)
        return tuple(hits)

    def _option_number(self, segment: str) -> Optional[int]:
        digit = OPTION_DIGIT_RE.match(segment)
        if digit:
            return int(digit.group(1))
        if DEFERRAL_RE.search(segment):
            return None
        ordinal = ORDINAL_RE.search(segment)
        if ordinal:
            return ORDINAL_WORDS[ordinal.group(1).lower()]
        if len(segment.split()) <= CARDINAL_MAX_WORDS:
            cardinal = CARDINAL_RE.search(segment)
            if cardinal:
                return CARDINAL_WORDS[cardinal.group(1).lower()]
        return None

    def narrate(self, event: NarrationEvent) -> str:
        template = _TEMPLATES[event.kind]
        return template(event.facts)


# Template helpers. Each phrases one event kind and adds no facts of its own.


def _fmt_date(iso: str) -> str:
    from datetime import date

    d = date.fromisoformat(iso)
    return "%s %d" % (d.strftime("%B"), d.day)


def _fmt_money(amount: float) -> str:
    return "$%.2f" % amount


def _status_report(f: dict) -> str:
    again = f.get("already_discussed")
    if f["status"] == "delivered":
        if again:
            return "That one — %s (%s) — was delivered on %s." % (
                f["title"], f["order_id"], _fmt_date(f["delivered_on"])
            )
        return "Your order %s (%s) was delivered on %s." % (
            f["order_id"], f["title"], _fmt_date(f["delivered_on"])
        )
    if again:
        # Same facts, said as a continuation. The customer already knows which
        # book it is; repeating the whole sentence back reads as not having
        # listened, even when the answer is genuinely unchanged.
        return (
            "Still on track — %s (%s) is with %s and is expected by %s."
            % (f["title"], f["order_id"], f["carrier"], _fmt_date(f["eta"]))
        )
    return (
        "Your order %s (%s) shipped with %s and is expected by %s."
        % (f["order_id"], f["title"], f["carrier"], _fmt_date(f["eta"]))
    )


def _numbered_options(options: List[dict]) -> str:
    parts = []
    for i, opt in enumerate(options, start=1):
        parts.append("%d) %s (%s)" % (i, opt["title"], opt["order_id"]))
    return "  ".join(parts)


def _clarify_which_order(f: dict) -> str:
    return (
        "Sure — which book would you like to return? %s"
        % _numbered_options(f["options"])
    )


def _reask_which_order(f: dict) -> str:
    return (
        "I can't guess here: a refund posts to one specific order, so I need "
        "to know which one. %s" % _numbered_options(f["options"])
    )


def _refund_approved(f: dict) -> str:
    # The posting time is a published service level carried on the event, not
    # a number written here. Without one the reply simply stops after the
    # refund rather than inventing a timeframe.
    posting = f.get("posting_target")
    return (
        "Done — %s (%s) was delivered on %s, inside the %d-day return "
        "window, so I've issued a refund of %s to your original payment "
        "method.%s"
        % (
            f["title"], f["order_id"], _fmt_date(f["delivered_on"]),
            f["window_days"], _fmt_money(f["amount"]),
            " It should post %s." % posting if posting else "",
        )
    )


def _return_denied(f: dict) -> str:
    if f["reason_code"] == "ORDER_NOT_DELIVERED":
        return (
            "%s (%s) hasn't been delivered yet, so I can't start a return "
            "for it. Once it arrives, I'd be happy to."
            % (f["title"], f["order_id"])
        )
    if f["reason_code"] == "ORDER_ALREADY_RETURNED":
        return (
            "It looks like %s (%s) was already returned on %s, so there's "
            "nothing further to send back on that order."
            % (f["title"], f["order_id"], _fmt_date(f["returned_on"]))
        )
    if f["reason_code"] == "ORDER_CANCELLED":
        return (
            "%s (%s) was cancelled before it shipped, so there's no delivery "
            "to return." % (f["title"], f["order_id"])
        )
    return (
        "%s (%s) was delivered on %s, which is outside the %d-day return "
        "window, so I can't issue a refund for it."
        % (
            f["title"], f["order_id"], _fmt_date(f["delivered_on"]),
            f["window_days"],
        )
    )


def _order_not_found(f: dict) -> str:
    return (
        "I can't find that order on your account. Could you double-check "
        "the order number?"
    )


def _escalation(f: dict) -> str:
    # Every branch quotes the same published response time, and it arrives as
    # a fact on the event rather than as a number written here — so a hosted
    # model, whose prompt forbids inventing facts, can state it too.
    when = f.get("response_target")
    promise = (
        "Someone will pick it up %s." % when if when else "You'll hear back "
        "shortly."
    )
    if f["reason_code"] == "ESCALATED_POLICY_DISPUTE":
        return (
            "I understand that's not the answer you wanted, and I can't "
            "change the policy outcome. I've escalated this to a human agent "
            "who can review it with you. %s" % promise
        )
    if f["reason_code"] == "ORDER_NOT_OWNED_BY_CUSTOMER":
        # Deliberately identical to the not-found wording: the reply must not
        # reveal whether a guessed order id exists. The escalation itself
        # travels on the back channel, not in the reply.
        return _order_not_found(f)
    if f["reason_code"] == "ESCALATED_CUSTOMER_REQUEST":
        return (
            "Of course — I've flagged this conversation for a human agent "
            "to pick up. %s" % promise
        )
    if f["reason_code"] == "ESCALATED_UNHANDLED":
        return (
            "This is beyond what I can help with here, so I've passed you to "
            "a human colleague who can take it from here. %s" % promise
        )
    return (
        "I don't want to guess with a refund, so I've handed this to a "
        "human agent who can look at your account with you. %s" % promise
    )


def _return_parked(f: dict) -> str:
    return (
        "(I've set the return aside for now — say \"return\" whenever "
        "you'd like to pick it back up.)"
    )


def _kb_answer(f: dict) -> str:
    return f["summary"]


def _kb_miss(f: dict) -> str:
    return (
        "I don't have reliable information on that, and I'd rather say so "
        "than guess. I can connect you with a human agent if that would "
        "help."
    )


def _no_returnable_orders(f: dict) -> str:
    # "No delivered orders" would be false for a customer with thirty-four of
    # them and simply none still inside the window. Say the true thing.
    if f.get("delivered_count"):
        return (
            "None of your delivered orders are still inside the %d-day return "
            "window, so there's nothing I can start a return on. If you think "
            "one should qualify, tell me which and I'll take a look."
            % f["window_days"]
        )
    return "I don't see any delivered orders on your account to return."


def _refund_status(f: dict) -> str:
    refund, posting = f.get("refund"), f.get("posting_target")
    if refund:
        when = " It should post %s." % posting if posting else ""
        if f.get("asked_to_return"):
            return (
                "That one's already taken care of — I refunded %s (%s) for %s "
                "earlier in this conversation.%s"
                % (
                    refund["title"], refund["order_id"],
                    _fmt_money(refund["amount"]), when,
                )
            )
        return (
            "Your refund of %s for %s (%s) is on its way to your original "
            "payment method.%s"
            % (
                _fmt_money(refund["amount"]), refund["title"],
                refund["order_id"], when,
            )
        )
    # Nothing was issued here, so do not imply one exists. State the published
    # commitment and offer a person, which is what the customer needs if they
    # are chasing a refund from an earlier conversation.
    if posting:
        return (
            "I haven't issued a refund on this conversation. Refunds post %s "
            "once they're approved — if you're waiting on one from earlier, I "
            "can put you through to a human who can look it up." % posting
        )
    return (
        "I haven't issued a refund on this conversation. I can put you "
        "through to a human who can look up an earlier one."
    )


def _order_history(f: dict) -> str:
    total, recent, more = f["total"], f["recent"], f["more"]
    if not total:
        return "I don't see any orders on your account yet."
    listed = ", ".join("%s (%s)" % (o["title"], o["order_id"]) for o in recent)
    tail = (
        " There are %d more — ask me about any of them by title." % more
        if more
        else ""
    )
    return (
        "You've placed %d order%s with us. The most recent %s: %s.%s"
        % (
            total, "" if total == 1 else "s",
            "is" if len(recent) == 1 else "are", listed, tail,
        )
    )


def _agent_identity(f: dict) -> str:
    name = f.get("name") or "the support agent here"
    return (
        "My name is %s. I can check an order's status, start a return or "
        "refund, and answer questions about shipping, returns and your "
        "account — anything I can't settle goes to a human colleague." % name
    )


def _help(f: dict) -> str:
    return (
        "Happy to help. I can check an order's status, start a return or "
        "refund, or answer questions about shipping, returns, and your "
        "account. What would you like to do?"
    )


def _out_of_scope(f: dict) -> str:
    # Names the limit and offers the way out. The agent does not pretend to
    # handle the request, and does not silently answer the nearest thing it can.
    return (
        "I'm not able to help with that here. I can check an order's status, "
        "start a return or refund, or answer questions about shipping, "
        "returns, and your account — or I can connect you with a person."
    )


_TEMPLATES = {
    "status_report": _status_report,
    "clarify_which_order": _clarify_which_order,
    "reask_which_order": _reask_which_order,
    "refund_approved": _refund_approved,
    "return_denied": _return_denied,
    "order_not_found": _order_not_found,
    "escalation": _escalation,
    "return_parked": _return_parked,
    "kb_answer": _kb_answer,
    "kb_miss": _kb_miss,
    "no_returnable_orders": _no_returnable_orders,
    "refund_status": _refund_status,
    "order_history": _order_history,
    "agent_identity": _agent_identity,
    "help": _help,
    "out_of_scope": _out_of_scope,
}


# ---------------------------------------------------------------------------
# Hosted model providers. Reached only when a vendor key is set.
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """\
You extract structured slots from one customer-support turn for an online
bookstore. Split the turn into requests at conjunctions and sentence breaks.
For each request report: intent (one of "order_status" for one specific
order, "order_history" for a question about the account as a whole — how many
orders, what they have bought, their order history — "agent_identity" for who
or what you are, "refund_status", "return_request", "policy_question",
"human_handoff", "out_of_scope", or null), order_id (format BK-0000, or
null), title_words (words from the customer's own order titles listed below
that the request refers to), option_number (if the request answers a
numbered choice, else null), and text (the request verbatim).

"refund_status" and "return_request" are easy to cross because both mention
money and both can contain the word "refund" — the test is whether the
customer is asking about money that already moved or demanding money move.
Use "refund_status" only for a question about a refund the agent already
granted — "where's my refund", "has it come through yet", "how long does it
take". Use "return_request" for anything that asks the agent to send a book
back or issue a refund, including a customer disputing a denial they were
just given: "refund it anyway", "I don't care what the policy says, just
refund me", and "give me my money back" are all "return_request", never
"refund_status" — nothing has been granted yet, so there is no status to ask
about. If the turn follows a decision the agent just denied, read a renewed
demand for money as "return_request", not as a question about one already
issued.

Use "out_of_scope" for a genuine request this bookstore's support does not
cover — anything that is not about an order, a return, a refund, shipping or
returns policy, the customer's order history, or who you are. Use it only when
the request fits none of the other intents. NEVER force a request onto the
nearest intent to avoid "out_of_scope", and never use "out_of_scope" for a
request one of the listed intents already covers — the listed intents always
win. Use null only when the text carries no request at all — a greeting, a
thanks, filler.

Customer's order titles: {titles}
Pending question from the agent: {pending}

Reply with ONLY a JSON array of request objects. Do not answer the customer.
Do not judge eligibility. Extract; nothing else."""

def _narration_system_prompt() -> str:
    """The narrator's brief, assembled from the profile.

    The persona lives in data so the stand-in and a hosted model speak with
    one voice — and so changing who the agent is stays a data edit. The two
    rules underneath it never move: no facts the event does not contain, and
    no softening of the decision. A persona may change the wording; it may
    not reach the verdict, and there is nothing here for it to reach.
    """
    who = AGENT.get("persona") or (
        "You are the voice of %s customer support: warm, plain, brief."
        % BRAND.get("display_name", "our")
    )
    return (
        "%s\n\n"
        "You will receive one structured event describing a decision or fact "
        "that has already been made. Phrase it for the customer in one to "
        "three sentences. You must not add facts, amounts, dates, or "
        "promises that are not in the event — if the event carries a "
        "response time, state it; if it does not, do not invent one. Write "
        "dates the way a person says them — \"July 18\", not "
        "\"2026-07-18\". You must not change or soften the decision." % who
    )


NARRATION_SYSTEM_PROMPT = _narration_system_prompt()


# Narration is untrusted the same way extraction is, and the incident this
# guards against is specific: an escalation was correctly recorded, the
# narrator was handed {"refund": None}, and wrote "your refund has been
# approved" anyway. The prompt above already says "you must not add facts
# that are not in the event" — that sentence alone did not stop it.
#
# The check is deliberately narrow: not "does the prose match the decision"
# in general, only "does the text claim a refund was granted, and if so, do
# the facts actually grant one". Narrower checks are easier to get right and
# this is the one claim that has already gone wrong once.
_REFUND_GRANT_VERB_RE = re.compile(
    r"\b(approved|issued|processed|posted|granted)\b", re.IGNORECASE
)
_REFUND_ON_ITS_WAY_RE = re.compile(r"\bon (?:its|the) way\b", re.IGNORECASE)

# The same negation vocabulary a denial actually uses ("I can't issue a
# refund", "hasn't been approved") — checked in the gap between the refund
# noun and the grant verb, so a denial reads as a denial rather than tripping
# the same detector that catches a claim.
# "n't" is deliberately not inside the \b(...)\b group: a word boundary sits
# between "e" and "n" in "haven't" only if something splits them, and nothing
# does — \bn't\b never matches an embedded contraction. Left as a bare
# substring, it catches every contraction in one alternative instead of
# enumerating "haven't", "hasn't", "wasn't", "isn't"... one at a time.
_NEGATION_RE = re.compile(
    r"\b(not|never|unable|cannot|no)\b|n't", re.IGNORECASE
)


def _claims_a_refund_was_granted(text: str) -> bool:
    """Does the sentence assert money already moved, or is about to?

    A refund noun near a grant verb ("approved", "issued", "processed",
    "posted", "granted") or "on its way" — backed off if a negation sits
    between them. The gap is generous (order titles run long: "for Godel,
    Escher, Bach (BK-1042) is on its way" has to still match) but stops at
    sentence punctuation, same as the rules provider's own regexes.

    "refunded" is checked on its own, not as "refund" plus a suffix: \\b does
    not split "refund" from "ed" inside one word, so the noun-anchored scan
    above never sees it, and "I've refunded you" would otherwise walk straight
    past every check here.
    """
    for match in re.finditer(r"\brefunds?\b", text, re.IGNORECASE):
        behind = text[max(0, match.start() - 40) : match.start()]
        if _NEGATION_RE.search(behind):
            # "No refund has been issued" — the noun itself is negated, so
            # nothing that follows it can turn this into a claim.
            continue
        # Sentence-final punctuation only — a decimal amount like $22.50
        # must not read as the end of the sentence.
        ahead = re.split(
            r"(?<!\d)[.?!](?!\d)", text[match.end() : match.end() + 120]
        )[0]
        verb = (
            _REFUND_GRANT_VERB_RE.search(ahead)
            or _REFUND_ON_ITS_WAY_RE.search(ahead)
        )
        if verb and not _NEGATION_RE.search(ahead[: verb.start()]):
            return True
        # "issued a refund" — the verb lands before the noun instead of after.
        if re.search(r"\bissued\b", behind, re.IGNORECASE):
            return True
    for match in re.finditer(r"\brefunded\b", text, re.IGNORECASE):
        behind = text[max(0, match.start() - 40) : match.start()]
        if not _NEGATION_RE.search(behind):
            return True
    return False


def _narration_is_grounded(kind: str, facts: dict, text: str) -> bool:
    """A refund-granted claim is grounded only where the facts grant one.

    `refund_approved` always carries a real `amount` — it exists to say
    exactly this, so it is never checked further. `refund_status` is
    grounded only when `facts["refund"]` names a refund already issued
    earlier in this conversation — the exact fact that was `None` in the
    incident. Every other kind never carries a refund fact at all, so a
    claim there is false by construction, whatever the words.

    Keyed on the facts, not the kind, on purpose: a genuine refund_status
    narration with a real prior refund legitimately says "your refund is on
    its way", and a kind-only check would reject that correct sentence along
    with the false one.
    """
    if not _claims_a_refund_was_granted(text):
        return True
    if kind == "refund_approved":
        return True
    return kind == "refund_status" and bool(facts.get("refund"))


# Models change names faster than this repo will; both are overridable so a
# stale default is a one-line env fix rather than a code change.
#
# A mini-tier model is the deliberate default, not a cost compromise. Both
# jobs are narrow and structured — turn to slots, event to sentence — which
# is the same property that makes the regex stand-in serviceable. A model
# that has to reason about eligibility would need the frontier tier; one
# that only reads and phrases does not.
ANTHROPIC_MODEL = os.environ.get("BOOKLY_ANTHROPIC_MODEL", "claude-sonnet-5")
OPENAI_MODEL = os.environ.get("BOOKLY_OPENAI_MODEL", "gpt-5.4-mini")

# Both jobs emit at most a short JSON array or three sentences, so the cap is
# generous. It is larger on the OpenAI side because that budget also covers
# reasoning tokens on current models — too small a cap there spends the whole
# allowance on reasoning and returns empty content rather than an error.
ANTHROPIC_MAX_OUTPUT_TOKENS = 700
OPENAI_MAX_OUTPUT_TOKENS = 4000

# Both SDKs default to waiting minutes. That is wrong for a demo: a hung call
# looks identical to a broken one from the stage, and the recovery is the
# same either way. Fail fast enough to switch back to the stand-in.
HOSTED_TIMEOUT_SECONDS = 20

# Models sometimes wrap JSON in a markdown fence despite being told not to.
JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class HostedProvider:
    """Everything a hosted model needs except the network call.

    Prompt construction, output validation, and the untrusted-output rules
    live here, so a new vendor is one subclass supplying one method. That is
    the whole reason swapping providers cannot change a decision: no
    subclass is given anywhere to put one.
    """

    name = "hosted"

    def extract(self, text: str, context: ExtractionContext) -> List[Request]:
        system = EXTRACTION_SYSTEM_PROMPT.format(
            titles=", ".join(context.known_titles) or "(none)",
            pending=self._describe_pending(context.pending),
        )
        return self._parse_requests(self._complete(system, text), text)

    def narrate(self, event: NarrationEvent) -> str:
        payload = json.dumps({"kind": event.kind, "facts": event.facts})
        text = self._complete(NARRATION_SYSTEM_PROMPT, payload).strip()
        if _narration_is_grounded(event.kind, event.facts, text):
            return text
        # The model claimed a refund the facts do not grant. Same shape as
        # untrusted extraction output: drop it and fall back — here, to the
        # sentence the stand-in would have said, built from the same facts,
        # rather than to an empty request.
        return _TEMPLATES[event.kind](event.facts)

    def _complete(self, system: str, user: str) -> str:
        raise NotImplementedError  # the one thing a vendor subclass supplies

    def verify(self) -> None:
        """One tiny call, to find out now rather than mid-conversation.

        Constructing a client is offline for both vendors, so a wrong key, a
        renamed model and a missing network all look like success until the
        first turn — which, on stage, is the worst possible moment to find
        out. This makes the smallest real request the provider can make.

        It deliberately goes through `_complete`, so it exercises the same
        path a turn does: the key, the model name, the parameter shape and
        the network, rather than only whether the credentials parse. Raises
        whatever the vendor raised; the caller decides what to say about it.
        """
        self._complete("Reply with the single word OK.", "ping")

    def _describe_pending(self, pending: Optional[PendingQuestion]) -> str:
        if pending is None:
            return "(none)"
        return "which order, options: %s" % ", ".join(pending.option_ids)

    def _parse_requests(self, raw: str, original_text: str) -> List[Request]:
        try:
            items = json.loads(JSON_FENCE_RE.sub("", raw))
        except ValueError:
            # An unparseable extraction yields one empty request; the agent
            # will ask rather than act on a guess.
            return [Request(intent=None, text=original_text)]
        if not isinstance(items, list):
            return [Request(intent=None, text=original_text)]
        requests = []
        for item in items:
            if isinstance(item, dict):
                requests.append(self._clean_request(item))
        return requests or [Request(intent=None, text=original_text)]

    def _clean_request(self, item: dict) -> Request:
        """Model output is untrusted: every field is validated to the same
        shapes the rules provider produces, or dropped."""
        intent = item.get("intent")
        order_id = item.get("order_id")
        option = item.get("option_number")
        words = item.get("title_words") or ()
        if isinstance(option, str) and option.isdigit():
            option = int(option)
        return Request(
            intent=intent if intent in VALID_INTENTS else None,
            order_id=(
                order_id.upper()
                if isinstance(order_id, str)
                and ORDER_ID_RE.fullmatch(order_id.strip())
                else None
            ),
            title_words=tuple(
                w.lower() for w in words if isinstance(w, str)
            ),
            option_number=(
                option
                if isinstance(option, int) and not isinstance(option, bool)
                else None
            ),
            text=str(item.get("text") or ""),
        )


class AnthropicProvider(HostedProvider):
    name = "anthropic"

    def __init__(self, api_key: Optional[str] = None) -> None:
        import anthropic  # imported here so the default path needs no deps

        # An explicit key is passed to the client rather than exported to the
        # environment. The console accepts a key for the session, and putting
        # it in os.environ would hand it to every subprocess the console
        # later spawns — including the one that runs the check suite.
        self._client = (
            anthropic.Anthropic(
                api_key=api_key, timeout=HOSTED_TIMEOUT_SECONDS
            )
            if api_key
            else anthropic.Anthropic(timeout=HOSTED_TIMEOUT_SECONDS)
        )

    def _complete(self, system: str, user: str) -> str:
        response = self._client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=ANTHROPIC_MAX_OUTPUT_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text


def _rejects_parameter(error: Exception, parameter: str) -> bool:
    """True when the API refused a request because of one named parameter."""
    message = str(error)
    return parameter in message and "not supported with this model" in message


class OpenAIProvider(HostedProvider):
    name = "openai"

    def __init__(self, api_key: Optional[str] = None) -> None:
        from openai import OpenAI  # imported here so the default path is clean

        # Same rule as the Anthropic provider: explicit, never exported.
        self._client = (
            OpenAI(api_key=api_key, timeout=HOSTED_TIMEOUT_SECONDS)
            if api_key
            else OpenAI(timeout=HOSTED_TIMEOUT_SECONDS)
        )
        # OpenAI renamed this parameter partway through the model line:
        # current models reject "max_tokens", older ones reject
        # "max_completion_tokens". Start on the current name and remember
        # what the API accepts, so the probe costs one failed call at most.
        self._budget_parameter = "max_completion_tokens"

    def _complete(self, system: str, user: str) -> str:
        # OpenAI carries the system prompt as the first message rather than a
        # separate field. That difference is the entire port.
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            return self._request(messages)
        except Exception as error:
            if not _rejects_parameter(error, self._budget_parameter):
                raise
            self._budget_parameter = (
                "max_tokens"
                if self._budget_parameter == "max_completion_tokens"
                else "max_completion_tokens"
            )
            return self._request(messages)

    def _request(self, messages: List[dict]) -> str:
        response = self._client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            **{self._budget_parameter: OPENAI_MAX_OUTPUT_TOKENS},
        )
        return response.choices[0].message.content or ""


PROVIDERS = {
    "rules": RulesProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}

# The model string each provider will actually call, so an interface can show
# it without holding its own copy that drifts from the code.
MODELS = {
    "rules": "regex extraction, template narration",
    "anthropic": ANTHROPIC_MODEL,
    "openai": OPENAI_MODEL,
}


def build_provider(name: str, api_key: Optional[str] = None) -> Provider:
    """Construct one provider by name, with an optional session key.

    `make_provider()` reads the environment and is what the CLI uses. This is
    what the console uses, because the console is handed a key at runtime and
    must never write it anywhere the environment can carry it onward.
    """
    if name not in PROVIDERS:
        raise ValueError(
            "unknown provider %r (expected one of %s)"
            % (name, ", ".join(sorted(PROVIDERS)))
        )
    provider_class = PROVIDERS[name]
    if issubclass(provider_class, HostedProvider):
        return provider_class(api_key)
    return provider_class()


def make_provider() -> Provider:
    """Hosted models are opt-in; the stand-in is the default path.

    BOOKLY_PROVIDER forces a choice; otherwise a single vendor key selects
    itself, and with no key set the demo runs on the stand-in.
    """
    name = os.environ.get("BOOKLY_PROVIDER") or _provider_from_keys()
    if name not in PROVIDERS:
        raise ValueError(
            "BOOKLY_PROVIDER must be one of %s (got %r)"
            % (", ".join(sorted(PROVIDERS)), name)
        )
    return PROVIDERS[name]()


# Which env var announces which vendor. Order is presentation only — an
# ambiguous environment is refused rather than resolved by precedence.
VENDOR_KEYS = (("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY"))
VENDOR_KEY_VARS = dict(VENDOR_KEYS)


def _provider_from_keys() -> str:
    """Guess the vendor from the environment, but only when the guess is
    unambiguous. Two keys set is a question, not a default — the same rule
    the agent applies to a customer turn it cannot resolve."""
    found = [name for name, var in VENDOR_KEYS if os.environ.get(var)]
    if len(found) > 1:
        raise ValueError(
            "%s are both set, so the provider is ambiguous. Set "
            "BOOKLY_PROVIDER to one of %s."
            % (
                " and ".join(var for _, var in VENDOR_KEYS),
                ", ".join(sorted(PROVIDERS)),
            )
        )
    return found[0] if found else "rules"
