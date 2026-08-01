"""Deterministic decision layer: verdicts, reason codes, and thresholds.

Every eligibility, escalation, and disambiguation rule the agent enforces is
computed here, in pure functions over order records. This module never imports
an LLM, never reads free text, and never sees a customer turn. If the language
model disappeared entirely, every function here would return the same answers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

from store import Order

Decision = Literal["approve_refund", "deny", "escalate", "not_found"]

# Thresholds live here, together, because they are policy — not code detail.

# Mirrors Bookly's published 30-day return policy. Day 30 itself is still
# eligible; the strict comparison in decide_return makes the bound inclusive.
RETURN_WINDOW_DAYS = 30

# Asking a clarifying question costs one turn. Guessing on a write path costs
# a wrong refund. We pay the turn — but not forever: after this many failed
# clarification attempts, a human takes over.
MAX_CLARIFY_ATTEMPTS = 2

# A customer repeating a request the policy already denied is a dispute, and
# disputes are for humans. One denial is enough to trigger the handoff.
DENIALS_BEFORE_ESCALATION = 1

# Reason codes. Machine-readable, stable, and the only vocabulary in which
# the policy engine explains itself.
REFUND_APPROVED_IN_WINDOW = "REFUND_APPROVED_IN_WINDOW"
RETURN_WINDOW_EXPIRED = "RETURN_WINDOW_EXPIRED"
ORDER_NOT_DELIVERED = "ORDER_NOT_DELIVERED"
ORDER_ALREADY_RETURNED = "ORDER_ALREADY_RETURNED"
ORDER_CANCELLED = "ORDER_CANCELLED"
ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
ORDER_NOT_OWNED_BY_CUSTOMER = "ORDER_NOT_OWNED_BY_CUSTOMER"
ESCALATED_POLICY_DISPUTE = "ESCALATED_POLICY_DISPUTE"
ESCALATED_CLARIFY_LIMIT = "ESCALATED_CLARIFY_LIMIT"
ESCALATED_CUSTOMER_REQUEST = "ESCALATED_CUSTOMER_REQUEST"


@dataclass(frozen=True)
class Verdict:
    """The policy engine's complete answer. Nothing downstream may add to it.

    The refund amount is copied from the order record here and nowhere else.
    No extracted slot has a path into this dataclass.
    """

    decision: Decision
    reason_code: str
    order_id: Optional[str] = None
    refund_amount: Optional[float] = None


def decide_return(
    order: Optional[Order], customer_id: str, today: date
) -> Verdict:
    """Return eligibility for one order, judged only from the order record."""
    if order is None:
        return Verdict("not_found", ORDER_NOT_FOUND)
    if order.customer_id != customer_id:
        # Externally this reads as "not found on your account"; internally the
        # distinct code routes it to a human in case it is an account issue.
        return Verdict("escalate", ORDER_NOT_OWNED_BY_CUSTOMER, order.order_id)
    # An order that already came back and one that never went out are both
    # "not delivered", but telling a customer their returned book "hasn't
    # arrived yet" is a confidently wrong sentence. They get their own codes
    # so the narrator has something true to say.
    if order.status == "returned":
        return Verdict("deny", ORDER_ALREADY_RETURNED, order.order_id)
    if order.status == "cancelled":
        return Verdict("deny", ORDER_CANCELLED, order.order_id)
    if order.status != "delivered" or order.delivered_on is None:
        return Verdict("deny", ORDER_NOT_DELIVERED, order.order_id)
    if (today - order.delivered_on).days > RETURN_WINDOW_DAYS:
        return Verdict("deny", RETURN_WINDOW_EXPIRED, order.order_id)
    return Verdict(
        "approve_refund",
        REFUND_APPROVED_IN_WINDOW,
        order.order_id,
        refund_amount=order.price_paid,
    )


def escalate_if_disputed(verdict: Verdict, prior_denials: int) -> Verdict:
    """Repeating a denied request escalates it; the verdict never flips."""
    if verdict.decision == "deny" and prior_denials >= DENIALS_BEFORE_ESCALATION:
        return Verdict("escalate", ESCALATED_POLICY_DISPUTE, verdict.order_id)
    return verdict


def can_view(order: Optional[Order], customer_id: str) -> bool:
    """A customer may see only their own orders. Applies to reads too."""
    return order is not None and order.customer_id == customer_id


def should_clarify(candidate_count: int) -> bool:
    """Ask only when there is a real choice. One candidate proceeds; zero is
    the nothing-to-return path, which the caller handles."""
    return candidate_count > 1


# A write may resolve on a title the customer typed, but only when the words
# they used actually identify a book. One ordinary word that happens to sit in
# a title is a coincidence, and acting on a coincidence refunds a book nobody
# named — "I'd like to return a book" once resolved to The Book of the New Sun
# and issued $31.50 against it, with no clarifying question asked.
#
# Two words is the fallback bound. It is the weaker of the two branches below,
# and a catalog whose titles are built out of ordinary words should say so in
# its generic word list rather than lean on it.
MIN_TITLE_WORDS_FOR_WRITE = 2


def title_reference_is_strong(matched: int, distinctive: int) -> bool:
    """Is a title match a reference a write may act on?

    Distinctive words identify a book. Generic ones do not, however many
    titles they happen to appear in — which is why this takes two counts
    rather than a list of words. Deciding *which* words are generic is a
    property of a catalog and lives in the profile; deciding *how much* is
    enough to move money is disambiguation, and disambiguation lives here
    beside `should_clarify`.

    A weak reference is not an error and not an ambiguity. The caller asks the
    clarifying question it would have asked if the customer had said nothing
    at all, because that is exactly how much they have said.
    """
    if distinctive >= 1:
        return True
    return matched >= MIN_TITLE_WORDS_FOR_WRITE


def clarify_limit_reached(attempts: int) -> bool:
    """The clarification budget is spent: the next step is a human, not a
    guess."""
    return attempts >= MAX_CLARIFY_ATTEMPTS


# ---------------------------------------------------------------------------
# What this module says about itself.
#
# These two registries are descriptive, not operative. Nothing above reads
# them and no verdict passes through them; deleting them would change no
# outcome. They exist so an interface can show a threshold without hardcoding
# it, and so any decision on screen can be traced back to the named constant
# that produced it — rather than the interface quietly growing its own copy
# of the number and drifting from the engine.
#
# `policy_constants_surface_matches_policy` asserts every entry here still
# equals the module attribute it names, so drift fails the suite loudly.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Constant:
    name: str
    value: object
    why: str


@dataclass(frozen=True)
class ReasonCode:
    code: str
    depends_on: tuple  # names of the Constants above, in effect order
    where: str  # what computes it
    gloss: str  # what it means, in one sentence a non-engineer can read


CONSTANTS = (
    Constant(
        "RETURN_WINDOW_DAYS",
        RETURN_WINDOW_DAYS,
        "Mirrors the published 30-day return policy. Day 30 itself is still "
        "eligible; the strict comparison in decide_return makes it inclusive.",
    ),
    Constant(
        "MAX_CLARIFY_ATTEMPTS",
        MAX_CLARIFY_ATTEMPTS,
        "Asking costs a turn; guessing on a write path costs a wrong refund. "
        "We pay the turn, but not forever — then a human takes over.",
    ),
    Constant(
        "DENIALS_BEFORE_ESCALATION",
        DENIALS_BEFORE_ESCALATION,
        "A customer repeating a request the policy already denied is a "
        "dispute, and disputes are for humans. One denial is enough.",
    ),
)

REASON_CODES = (
    ReasonCode(
        REFUND_APPROVED_IN_WINDOW, ("RETURN_WINDOW_DAYS",), "decide_return",
        "Delivered, owned by this customer, and inside the return window.",
    ),
    ReasonCode(
        RETURN_WINDOW_EXPIRED, ("RETURN_WINDOW_DAYS",), "decide_return",
        "Delivered, but longer ago than the return window allows.",
    ),
    ReasonCode(
        ORDER_NOT_DELIVERED, (), "decide_return",
        "Still on its way, so there is nothing to send back yet.",
    ),
    ReasonCode(
        ORDER_ALREADY_RETURNED, (), "decide_return",
        "Already came back on an earlier return.",
    ),
    ReasonCode(
        ORDER_CANCELLED, (), "decide_return",
        "Cancelled before it shipped, so no delivery ever happened.",
    ),
    ReasonCode(
        ORDER_NOT_FOUND, (), "decide_return",
        "No such order.",
    ),
    ReasonCode(
        ORDER_NOT_OWNED_BY_CUSTOMER, (), "decide_return",
        "The order exists but belongs to someone else. Routed to a human in "
        "case it is an account problem; the customer is told only that it "
        "was not found, so a guessed order id reveals nothing.",
    ),
    ReasonCode(
        ESCALATED_POLICY_DISPUTE, ("DENIALS_BEFORE_ESCALATION",),
        "escalate_if_disputed",
        "The customer pressed a request the policy already denied. The "
        "verdict does not flip; a human picks it up.",
    ),
    ReasonCode(
        ESCALATED_CLARIFY_LIMIT, ("MAX_CLARIFY_ATTEMPTS",),
        "clarify_limit_reached, applied by the agent",
        "The clarifying question was asked as often as it is worth asking.",
    ),
    ReasonCode(
        ESCALATED_CUSTOMER_REQUEST, (), "the agent, on an explicit request",
        "The customer asked for a person.",
    ),
)

_CONSTANTS_BY_NAME = {constant.name: constant for constant in CONSTANTS}
_REASON_CODES_BY_CODE = {entry.code: entry for entry in REASON_CODES}


def constants_for(reason_code: str) -> list:
    """The named constants a verdict's reason code rests on, so a decision on
    screen can be traced to the line of policy that produced it."""
    entry = _REASON_CODES_BY_CODE.get(reason_code)
    if entry is None:
        return []
    return [
        {
            "name": name,
            "value": _CONSTANTS_BY_NAME[name].value,
            "why": _CONSTANTS_BY_NAME[name].why,
        }
        for name in entry.depends_on
    ]


def describe(reason_code: str) -> Optional[dict]:
    """Everything this module can say about one reason code."""
    entry = _REASON_CODES_BY_CODE.get(reason_code)
    if entry is None:
        return None
    return {
        "code": entry.code,
        "where": entry.where,
        "gloss": entry.gloss,
        "constants": constants_for(entry.code),
    }
