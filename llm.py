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


VALID_INTENTS = frozenset(
    ["order_status", "return_request", "policy_question", "human_handoff"]
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

# Catches general questions the knowledge base might answer. Retrieval makes
# the real relevance call; this only routes the segment.
POLICY_QUESTION_RE = re.compile(
    r"\b(policy|policies|shipping|password|how long|business days)\b",
    re.IGNORECASE,
)

# Catches an explicit ask for a person. Kept narrow: it must not fire on
# ordinary mentions of people, only on asking to be handed to one.
HUMAN_HANDOFF_RE = re.compile(
    r"\b(manager|supervisor|representative|escalate"
    r"|(?:speak|talk) to (?:a |an )?(?:person|human|someone|somebody|agent))\b",
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
        # Asking to do something beats asking about something: a segment that
        # requests a return wins even if it also mentions the policy.
        if RETURN_REQUEST_RE.search(segment):
            return "return_request"
        if HUMAN_HANDOFF_RE.search(segment):
            return "human_handoff"
        # An explicit order reference — an id or a title — counts as the
        # object: "what's the status of Dune" is a status question even
        # without the word "order".
        if STATUS_SIGNAL_RE.search(segment) and (
            STATUS_OBJECT_RE.search(segment) or has_reference
        ):
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
        # Deliberately identical to the not-found wording: the reply must not
        # reveal whether a guessed order id exists. The escalation itself
        # travels on the back channel, not in the reply.
        return _order_not_found(f)
    if f["reason_code"] == "ESCALATED_CUSTOMER_REQUEST":
        return (
            "Of course — I've flagged this conversation for a human agent "
            "to pick up. They'll follow up with you shortly."
        )
    return (
        "I don't want to guess with a refund, so I've handed this to a "
        "human agent who can look at your account with you."
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
    "return_parked": _return_parked,
    "kb_answer": _kb_answer,
    "kb_miss": _kb_miss,
    "no_returnable_orders": _no_returnable_orders,
    "help": _help,
}


# ---------------------------------------------------------------------------
# Hosted model providers. Reached only when a vendor key is set.
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """\
You extract structured slots from one customer-support turn for an online
bookstore. Split the turn into requests at conjunctions and sentence breaks.
For each request report: intent (one of "order_status", "return_request",
"policy_question", "human_handoff", or null), order_id (format BK-0000, or
null), title_words
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
        return self._complete(NARRATION_SYSTEM_PROMPT, payload).strip()

    def _complete(self, system: str, user: str) -> str:
        raise NotImplementedError  # the one thing a vendor subclass supplies

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

    def __init__(self) -> None:
        import anthropic  # imported here so the default path needs no deps

        self._client = anthropic.Anthropic()

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

    def __init__(self) -> None:
        from openai import OpenAI  # imported here so the default path is clean

        self._client = OpenAI()
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
