"""Mock data layer: customers, orders, and the policy knowledge base.

Everything the agent treats as ground truth lives here. The frozen clock
(TODAY) lives with the mock data so that demos and tests are deterministic.
This module must never contain decision logic; it only holds records.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

# The demo clock. Policy functions take `today` as a parameter so they stay
# pure; this constant is the single place the demo's "now" is defined.
TODAY = date(2026, 7, 30)

# The customer on the other end of every demo conversation.
CURRENT_CUSTOMER_ID = "C-1001"


@dataclass(frozen=True)
class Order:
    order_id: str
    customer_id: str
    title: str
    author: str
    price_paid: float
    status: str  # "shipped" or "delivered"
    ordered_on: date
    delivered_on: Optional[date] = None
    eta: Optional[date] = None
    carrier: Optional[str] = None


@dataclass(frozen=True)
class Article:
    article_id: str
    title: str
    summary: str
    keywords: frozenset


ORDERS = {
    "BK-1041": Order(
        order_id="BK-1041",
        customer_id="C-1001",
        title="Dune",
        author="Frank Herbert",
        price_paid=18.99,
        status="shipped",
        ordered_on=date(2026, 7, 24),
        eta=date(2026, 8, 1),
        carrier="Bookly Express",
    ),
    "BK-1042": Order(
        order_id="BK-1042",
        customer_id="C-1001",
        title="Godel, Escher, Bach",
        author="Douglas Hofstadter",
        price_paid=22.50,
        status="delivered",
        ordered_on=date(2026, 7, 14),
        delivered_on=date(2026, 7, 18),
    ),
    "BK-0987": Order(
        order_id="BK-0987",
        customer_id="C-1001",
        title="The Pragmatic Programmer",
        author="David Thomas and Andrew Hunt",
        price_paid=39.99,
        status="delivered",
        ordered_on=date(2026, 4, 28),
        delivered_on=date(2026, 5, 2),
    ),
    # Belongs to a different customer. Exists so ownership checks are testable.
    "BK-2077": Order(
        order_id="BK-2077",
        customer_id="C-2002",
        title="Snow Crash",
        author="Neal Stephenson",
        price_paid=17.25,
        status="delivered",
        ordered_on=date(2026, 7, 16),
        delivered_on=date(2026, 7, 20),
    ),
}

# The knowledge base is deliberately small and deliberately has gaps
# (no international shipping article, for example). Retrieval must fail
# closed on the gaps rather than serve the nearest neighbor.
ARTICLES = [
    Article(
        article_id="kb-shipping-times",
        title="Standard shipping times",
        summary=(
            "Standard shipping takes 3 to 5 business days within the "
            "continental US. Expedited shipping takes 1 to 2 business days."
        ),
        keywords=frozenset(
            ["shipping", "ship", "delivery", "deliver", "arrive", "long",
             "take", "takes", "standard", "expedited", "fast", "days"]
        ),
    ),
    Article(
        article_id="kb-return-policy",
        title="Return policy",
        summary=(
            "Books can be returned within 30 days of delivery for a full "
            "refund to the original payment method. Refunds post within 5 "
            "business days of approval."
        ),
        keywords=frozenset(
            ["return", "returns", "refund", "refunds", "policy", "window",
             "days", "exchange", "money", "back"]
        ),
    ),
    Article(
        article_id="kb-password-reset",
        title="Password reset",
        summary=(
            "To reset your password, use the 'Forgot password' link on the "
            "sign-in page. A reset email arrives within a few minutes; check "
            "spam if it does not."
        ),
        keywords=frozenset(
            ["password", "reset", "login", "log", "sign", "account",
             "forgot", "email"]
        ),
    ),
]
