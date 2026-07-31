"""The language model boundary. The model has exactly two jobs.

Extraction: one customer turn in, structured slots out.
Narration: one structured decision in, English out.

Nothing else crosses this boundary. No verdict, amount, or reason code is
ever produced here — those come from policy.py, which this module never
imports. The default provider is a rules-based stand-in so the demo runs
with no dependencies and no API key; because both jobs are narrow and
structured, the stand-in is serviceable. Setting ANTHROPIC_API_KEY swaps in
the hosted model without changing any other file.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

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

    intent: Optional[str]  # "order_status", "return_request",
    #                        "policy_question", or None
    order_id: Optional[str] = None
    title_words: Tuple[str, ...] = ()
    option_number: Optional[int] = None
    text: str = ""


@dataclass(frozen=True)
class NarrationEvent:
    """A structured fact bundle for the model to phrase. Facts only."""

    kind: str
    facts: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rules-based stand-in provider.
# ---------------------------------------------------------------------------

# A turn splits into requests at conjunctions and sentence breaks, so each
# request carries its own slots ("where's my Dune order AND I want to
# return the Escher book" becomes two requests).
SEGMENT_SPLIT_RE = re.compile(r"\s+\band\b\s+|;\s*|\.\s+")

# Catches order ids and nothing else. The format is fixed by the store.
ORDER_ID_RE = re.compile(r"\bBK-\d{4}\b", re.IGNORECASE)

# Catches asking to send something back. Deliberately does not catch
# "return policy" / "refund policy" — those are questions, not requests.
RETURN_REQUEST_RE = re.compile(
    r"\b(return|refund|send (?:it |them )?back|money back)\b(?!\s+polic)",
    re.IGNORECASE,
)

# Order status needs both a question word and an object, so "how long does
# shipping take" (no object) stays a policy question.
STATUS_SIGNAL_RE = re.compile(
    r"\b(where|status|track|tracking|when|arrive|arriving|arrived|eta)\b",
    re.IGNORECASE,
)
STATUS_OBJECT_RE = re.compile(
    r"\b(order|package|delivery|it|book|copy)\b", re.IGNORECASE
)

# Catches general questions the knowledge base might answer. Retrieval makes
# the real relevance call; this only routes the segment.
POLICY_QUESTION_RE = re.compile(
    r"\b(policy|policies|shipping|password|how long|business days)\b",
    re.IGNORECASE,
)

# Catches an answer to "which one?": a bare number or an ordinal word.
OPTION_DIGIT_RE = re.compile(r"^\s*(?:option\s+)?([1-9])\s*[.)]?\s*$")
ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3}
ORDINAL_RE = re.compile(r"\b(first|second|third)\b", re.IGNORECASE)

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
        return Request(
            intent=self._intent_of(segment),
            order_id=id_match.group(0).upper() if id_match else None,
            title_words=self._title_words(segment, context.known_titles),
            option_number=self._option_number(segment),
            text=segment.strip(),
        )

    def _intent_of(self, segment: str) -> Optional[str]:
        # Asking to do something beats asking about something: a segment that
        # requests a return wins even if it also mentions the policy.
        if RETURN_REQUEST_RE.search(segment):
            return "return_request"
        if STATUS_SIGNAL_RE.search(segment) and STATUS_OBJECT_RE.search(segment):
            return "order_status"
        if POLICY_QUESTION_RE.search(segment):
            return "policy_question"
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
        ordinal = ORDINAL_RE.search(segment)
        if ordinal:
            return ORDINAL_WORDS[ordinal.group(1).lower()]
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
    if f["status"] == "delivered":
        return "Your order %s (%s) was delivered on %s." % (
            f["order_id"], f["title"], _fmt_date(f["delivered_on"])
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
    return (
        "Done — %s (%s) was delivered on %s, inside the %d-day return "
        "window, so I've issued a refund of %s to your original payment "
        "method. It should post within 5 business days."
        % (
            f["title"], f["order_id"], _fmt_date(f["delivered_on"]),
            f["window_days"], _fmt_money(f["amount"]),
        )
    )


def _return_denied(f: dict) -> str:
    if f["reason_code"] == "ORDER_NOT_DELIVERED":
        return (
            "%s (%s) hasn't been delivered yet, so I can't start a return "
            "for it. Once it arrives, I'd be happy to."
            % (f["title"], f["order_id"])
        )
    return (
        "I'm sorry — %s (%s) was delivered on %s, which is outside the "
        "%d-day return window, so I can't issue a refund for it."
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
    if f["reason_code"] == "ESCALATED_POLICY_DISPUTE":
        return (
            "I understand that's not the answer you wanted. I can't change "
            "the policy outcome, so I've escalated this to a human agent "
            "who can review it with you — you'll hear back shortly."
        )
    if f["reason_code"] == "ORDER_NOT_OWNED_BY_CUSTOMER":
        return (
            "I can't find that order on your account, so I've flagged it "
            "for a human agent to review with you."
        )
    return (
        "I don't want to guess with a refund, so I've handed this to a "
        "human agent who can look at your account with you."
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
    return "I don't see any delivered orders on your account to return."


def _help(f: dict) -> str:
    return (
        "I can check an order's status, start a return or refund, or answer "
        "questions about shipping, returns, and your account. What can I "
        "do for you?"
    )


_TEMPLATES = {
    "status_report": _status_report,
    "clarify_which_order": _clarify_which_order,
    "reask_which_order": _reask_which_order,
    "refund_approved": _refund_approved,
    "return_denied": _return_denied,
    "order_not_found": _order_not_found,
    "escalation": _escalation,
    "kb_answer": _kb_answer,
    "kb_miss": _kb_miss,
    "no_returnable_orders": _no_returnable_orders,
    "help": _help,
}


# ---------------------------------------------------------------------------
# Hosted model provider. Reached only when ANTHROPIC_API_KEY is set.
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """\
You extract structured slots from one customer-support turn for an online
bookstore. Split the turn into requests at conjunctions and sentence breaks.
For each request report: intent (one of "order_status", "return_request",
"policy_question", or null), order_id (format BK-0000, or null), title_words
(words from the customer's own order titles listed below that the request
refers to), option_number (if the request answers a numbered choice, else
null), and text (the request verbatim).

Customer's order titles: {titles}
Pending question from the agent: {pending}

Reply with ONLY a JSON array of request objects. Do not answer the customer.
Do not judge eligibility. Extract; nothing else."""

NARRATION_SYSTEM_PROMPT = """\
You are the voice of Bookly customer support: warm, plain, brief. You will
receive one structured event describing a decision or fact that has already
been made. Phrase it for the customer in one to three sentences. You must
not add facts, amounts, dates, or promises that are not in the event. You
must not change or soften the decision."""


class AnthropicProvider:
    """Same two jobs, hosted model. Decisions still never cross into here."""

    name = "anthropic"

    def __init__(self) -> None:
        import anthropic  # imported here so the default path needs no deps

        self._client = anthropic.Anthropic()
        self._model = "claude-sonnet-5"

    def extract(self, text: str, context: ExtractionContext) -> List[Request]:
        system = EXTRACTION_SYSTEM_PROMPT.format(
            titles=", ".join(context.known_titles) or "(none)",
            pending=self._describe_pending(context.pending),
        )
        raw = self._complete(system, text)
        return self._parse_requests(raw, text)

    def narrate(self, event: NarrationEvent) -> str:
        payload = json.dumps({"kind": event.kind, "facts": event.facts})
        return self._complete(NARRATION_SYSTEM_PROMPT, payload).strip()

    def _complete(self, system: str, user: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=700,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text

    def _describe_pending(self, pending: Optional[PendingQuestion]) -> str:
        if pending is None:
            return "(none)"
        return "which order, options: %s" % ", ".join(pending.option_ids)

    def _parse_requests(self, raw: str, original_text: str) -> List[Request]:
        try:
            items = json.loads(raw)
        except ValueError:
            # An unparseable extraction yields one empty request; the agent
            # will ask rather than act on a guess.
            return [Request(intent=None, text=original_text)]
        requests = []
        for item in items:
            requests.append(
                Request(
                    intent=item.get("intent"),
                    order_id=item.get("order_id"),
                    title_words=tuple(item.get("title_words") or ()),
                    option_number=item.get("option_number"),
                    text=item.get("text") or "",
                )
            )
        return requests or [Request(intent=None, text=original_text)]


def make_provider():
    """The hosted model is opt-in; the stand-in is the default path."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicProvider()
    return RulesProvider()
