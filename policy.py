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


def clarify_limit_reached(attempts: int) -> bool:
    """The clarification budget is spent: the next step is a human, not a
    guess."""
    return attempts >= MAX_CLARIFY_ATTEMPTS
