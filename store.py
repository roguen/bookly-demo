"""Mock data layer: customers, orders, and the policy knowledge base.

Everything the agent treats as ground truth lives here — but none of it is
written here any more. The dataset is a *profile*: a JSON file under
`profiles/` holding the customer record, the orders, the knowledge base, the
catalog, the frozen clock, and the brand wordmark. This module loads one at
import and turns it into the same frozen dataclasses the rest of the repo
already reads, so nothing downstream knows the data moved.

The reason is re-skinning. Standing this demo up for a different company is a
data edit measured in minutes rather than a code edit: `BOOKLY_PROFILE`
selects the active profile and no other file changes.

What deliberately did *not* move into the profile: thresholds and reason
codes. Those are policy, they live in `policy.py`, and a data file must not
be able to move them.

The frozen clock (TODAY) still lives with the mock data, because determinism
belongs to the fixtures. It just reads from the profile now.

This module must never contain decision logic; it only holds records.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

PROFILE_ENV_VAR = "BOOKLY_PROFILE"
DEFAULT_PROFILE = "bookly"
# Resolved against this file, not the working directory, so the console and
# the back office can be started from anywhere.
PROFILE_DIR = Path(__file__).resolve().parent / "profiles"


@dataclass(frozen=True)
class Order:
    order_id: str
    customer_id: str
    title: str
    author: str
    price_paid: float
    # "shipped", "delivered", "returned", or "cancelled". Only "delivered"
    # is returnable; policy.py is where that judgement is made, not here.
    status: str
    ordered_on: date
    delivered_on: Optional[date] = None
    returned_on: Optional[date] = None
    eta: Optional[date] = None
    carrier: Optional[str] = None
    # Catalog metadata, snapshotted onto the line the way a real order record
    # would hold it. Display only; no decision reads these.
    format: Optional[str] = None
    published: Optional[int] = None
    pages: Optional[int] = None


@dataclass(frozen=True)
class Contact:
    """One prior support contact. Record texture; nothing decides on it."""

    on: date
    channel: str
    subject: str
    outcome: str


@dataclass(frozen=True)
class Customer:
    customer_id: str
    name: str
    email: str
    member_since: date
    tier: str
    lifetime_value: float
    orders_placed: int
    payment_kind: str
    payment_last_four: str
    csat: float
    csat_responses: int
    contact_history: Tuple[Contact, ...] = ()


@dataclass(frozen=True)
class Article:
    article_id: str
    title: str
    summary: str
    keywords: FrozenSet[str]


# -- profile loading --------------------------------------------------------


def profile_path(name: Optional[str] = None) -> Path:
    """A bare name selects `profiles/<name>.json`; anything ending in .json
    is taken as a path, so a profile can live outside the repo."""
    name = name or os.environ.get(PROFILE_ENV_VAR) or DEFAULT_PROFILE
    if name.endswith(".json"):
        return Path(name).expanduser()
    return PROFILE_DIR / ("%s.json" % name)


def load_profile(name: Optional[str] = None) -> dict:
    path = profile_path(name)
    try:
        with open(path, "r", encoding="utf-8") as profile_file:
            return json.load(profile_file)
    except FileNotFoundError:
        raise FileNotFoundError(
            "profile not found: %s. Set %s to a name under profiles/ or to a "
            "path ending in .json." % (path, PROFILE_ENV_VAR)
        )


def _date(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def build_orders(profile: dict) -> Dict[str, Order]:
    """Insertion order is preserved, and it is load bearing: the order the
    clarifying question numbers its options in is the order given here."""
    titles = profile.get("catalog", {}).get("titles", {})
    orders = {}
    for record in profile["orders"]:
        meta = titles.get(record["title"], {})
        orders[record["order_id"]] = Order(
            order_id=record["order_id"],
            customer_id=record["customer_id"],
            title=record["title"],
            author=record["author"],
            price_paid=record["price_paid"],
            status=record["status"],
            ordered_on=_date(record["ordered_on"]),
            delivered_on=_date(record.get("delivered_on")),
            returned_on=_date(record.get("returned_on")),
            eta=_date(record.get("eta")),
            carrier=record.get("carrier"),
            format=meta.get("format"),
            published=meta.get("published"),
            pages=meta.get("pages"),
        )
    return orders


def build_customer(profile: dict) -> Customer:
    record = profile["customer"]
    return Customer(
        customer_id=record["customer_id"],
        name=record["name"],
        email=record["email"],
        member_since=_date(record["member_since"]),
        tier=record["tier"],
        lifetime_value=record["lifetime_value"],
        orders_placed=record["orders_placed"],
        payment_kind=record["payment_kind"],
        payment_last_four=record["payment_last_four"],
        csat=record["csat"],
        csat_responses=record["csat_responses"],
        contact_history=tuple(
            Contact(
                on=_date(contact["on"]),
                channel=contact["channel"],
                subject=contact["subject"],
                outcome=contact["outcome"],
            )
            for contact in record.get("contact_history", ())
        ),
    )


def build_articles(profile: dict) -> List[Article]:
    return [
        Article(
            article_id=record["article_id"],
            title=record["title"],
            summary=record["summary"],
            keywords=frozenset(record["keywords"]),
        )
        for record in profile["articles"]
    ]


# -- the active profile -----------------------------------------------------

PROFILE = load_profile()

BRAND = PROFILE["brand"]
CATALOG = PROFILE["catalog"]

# Who the agent says it is and how it declines. Voice, not behaviour: it
# reaches the narration prompt and the templates and never the decision
# layer. An empty dict here leaves the agent anonymous and plainly worded.
AGENT = PROFILE.get("agent", {})

# What the agent may promise a customer about timing. A published commitment,
# like the knowledge-base copy — not a threshold any decision reads.
SERVICE_LEVELS = PROFILE.get("service_levels", {})

# The demo clock. Policy functions take `today` as a parameter so they stay
# pure; this constant is the single place the demo's "now" is defined.
TODAY = _date(PROFILE["clock"]["today"])

# The customer on the other end of every demo conversation.
CUSTOMER = build_customer(PROFILE)
CURRENT_CUSTOMER_ID = CUSTOMER.customer_id

ORDERS = build_orders(PROFILE)

# The knowledge base is deliberately small and deliberately has gaps
# (no international shipping article, for example). Retrieval must fail
# closed on the gaps rather than serve the nearest neighbor.
ARTICLES = build_articles(PROFILE)
