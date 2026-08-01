"""Deterministic decision layer: verdicts, reason codes, and thresholds.

Every eligibility, escalation, and disambiguation rule the agent enforces is
computed here, in pure functions over order records. This module never imports
an LLM, never reads free text, and never sees a customer turn. If the language
model disappeared entirely, every function here would return the same answers.

The three CX thresholds are authorable (v3.2.0): their values are read from an
append-only policy document a non-engineer edits, and the defaults are the
historical policy. That is still not a place the model reaches, and the verdict
is still computed only here — a decision reads a number from a validated
document instead of a literal, which is a change of storage, not of authority.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from store import Order

Decision = Literal["approve_refund", "deny", "escalate", "not_found"]

# Thresholds live here, together, because they are policy — not code detail.
#
# As of v3.2.0 these three are *authorable*: their values come from an active
# policy document (resolved through the module `__getattr__` at the foot of this
# file), and a non-engineer edits them from the back office. The defaults below
# are the historical policy, so an un-edited build decides exactly as it always
# did — authoring changes where a number comes from, never where a verdict is
# computed, and the document holds only numbers this module already understood.
#
# Two floors are deliberately NOT here and NOT authorable: MIN_KEYWORD_MATCHES
# (tools.py) and MIN_TITLE_WORDS_FOR_WRITE (below). Both exist to stop a
# confidently wrong answer reaching a customer, and the whole point of a floor
# is that it does not get lowered — handing that dial to a non-engineer would
# re-open the exact failures they were added to close.


@dataclass(frozen=True)
class Parameter:
    """One authorable policy number: the name a decision reads it by, the key it
    is stored under in the document, its historical default, why it exists, and
    the inclusive bounds a non-engineer's edit is validated against before it can
    ever go active."""

    name: str
    key: str
    default: int
    why: str
    minimum: int
    maximum: int


PARAMETERS = (
    Parameter(
        "RETURN_WINDOW_DAYS", "return_window_days", 30,
        "Mirrors the published 30-day return policy. Day 30 itself is still "
        "eligible; the strict comparison in decide_return makes it inclusive.",
        0, 365,
    ),
    Parameter(
        "MAX_CLARIFY_ATTEMPTS", "max_clarify_attempts", 2,
        "Asking costs a turn; guessing on a write path costs a wrong refund. "
        "We pay the turn, but not forever — then a human takes over.",
        1, 5,
    ),
    Parameter(
        "DENIALS_BEFORE_ESCALATION", "denials_before_escalation", 1,
        "A customer repeating a request the policy already denied is a "
        "dispute, and disputes are for humans. One denial is enough.",
        1, 5,
    ),
)

_PARAMS_BY_NAME = {parameter.name: parameter for parameter in PARAMETERS}
POLICY_DEFAULTS = {parameter.name: parameter.default for parameter in PARAMETERS}


# ---------------------------------------------------------------------------
# The authored policy document.
#
# policy.json is an append-only change log — the same shape and the same ethos
# as the review queue: every edit carries who, what, why and when, and nothing
# is ever overwritten (a revert is another append). The active policy is the
# defaults with the log replayed in order, so an absent file is the historical
# policy exactly, and a clean clone decides as it always did. The file is read
# through an mtime cache, so an edit made in the back office is seen by the
# console on its next decision without a restart — the two processes share this
# file the way they already share the queue.
# ---------------------------------------------------------------------------

POLICY_PATH_ENV_VAR = "BOOKLY_POLICY_PATH"
_DEFAULT_POLICY_PATH = str(Path(__file__).resolve().parent / "policy.json")
_doc_lock = threading.RLock()
_doc_cache = {"key": None, "changes": []}


def policy_path() -> Path:
    return Path(os.environ.get(POLICY_PATH_ENV_VAR) or _DEFAULT_POLICY_PATH)


def _param_by_key(key) -> Optional[Parameter]:
    for parameter in PARAMETERS:
        if parameter.key == key:
            return parameter
    return None


def _load_changes() -> list:
    """The append-only change log, reloaded only when the file changes on disk.

    Keyed on (path, mtime) so pointing the env var at a different document — as
    the checks do — always reloads rather than trusting a stale cache.
    """
    path = policy_path()
    with _doc_lock:
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            # Absent file is the historical policy, not an error.
            _doc_cache["key"] = None
            _doc_cache["changes"] = []
            return []
        if key != _doc_cache["key"]:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                changes = raw.get("changes", []) if isinstance(raw, dict) else []
            except (OSError, ValueError):
                changes = []
            _doc_cache["key"] = key
            _doc_cache["changes"] = changes if isinstance(changes, list) else []
        return list(_doc_cache["changes"])


def _valid_value(parameter: Parameter, value) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and parameter.minimum <= value <= parameter.maximum
    )


def active_policy() -> dict:
    """The policy numbers in force right now, keyed by their constant name.

    The historical defaults with the authored change log replayed over them. An
    absent or empty document is exactly the historical policy — nothing here
    reasons, and the language model is nowhere near it. A stored value is
    re-checked against the parameter's bounds on the way out too, so a document
    hand-edited past a bound cannot push a threshold out of range: the bounds
    hold on read, not only on write.
    """
    values = dict(POLICY_DEFAULTS)
    for change in _load_changes():
        parameter = _param_by_key(change.get("field")) if isinstance(
            change, dict
        ) else None
        if parameter is not None and _valid_value(parameter, change.get("to")):
            values[parameter.name] = change["to"]
    return values


def policy_changes(field: Optional[str] = None) -> list:
    """The append-only change log, in the order it was written. Optionally for
    one field, so the surface can show a value's history beside it."""
    changes = [c for c in _load_changes() if isinstance(c, dict)]
    if field is not None:
        changes = [c for c in changes if c.get("field") == field]
    return changes


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def change_parameter(
    field: str, to_value, actor: str, justification: str,
    at: Optional[str] = None,
) -> dict:
    """Author one parameter change: validated, then appended — never overwritten.

    The same discipline the review queue applies to a human resolution — an
    actor and a justification are required, enforced here once rather than in
    the browser — and the same append-only shape, so a change is a new event
    that supersedes, and a revert is another event rather than an erasure. A
    change that fails validation never becomes active, and a value outside the
    parameter's declared bounds is refused: this is where "a non-engineer can
    tune the policy but cannot break it" actually lives.
    """
    parameter = _param_by_key(field)
    if parameter is None:
        raise ValueError("%r is not an authorable policy parameter" % (field,))
    if isinstance(to_value, bool) or not isinstance(to_value, int):
        raise ValueError("%s must be a whole number" % parameter.key)
    if not (parameter.minimum <= to_value <= parameter.maximum):
        raise ValueError(
            "%s must be between %d and %d"
            % (parameter.key, parameter.minimum, parameter.maximum)
        )
    actor = (actor or "").strip()
    justification = (justification or "").strip()
    if not actor:
        raise ValueError("an actor is required to change policy")
    if not justification:
        raise ValueError("a justification is required to change policy")
    with _doc_lock:
        event = {
            "field": parameter.key,
            "from": active_policy()[parameter.name],
            "to": to_value,
            "actor": actor,
            "justification": justification,
            "at": at or _utc_now(),
        }
        changes = _load_changes()
        changes.append(event)
        _write_changes(changes)
    return dict(event)


def _write_changes(changes: list) -> None:
    """Persist the log atomically, then refresh the cache to what was written."""
    path = policy_path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps({"changes": changes}, indent=2), encoding="utf-8"
    )
    tmp.replace(path)  # atomic on the same filesystem
    _doc_cache["key"] = (str(path), path.stat().st_mtime_ns)
    _doc_cache["changes"] = list(changes)

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
ESCALATED_UNHANDLED = "ESCALATED_UNHANDLED"


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
    if (today - order.delivered_on).days > active_policy()["RETURN_WINDOW_DAYS"]:
        return Verdict("deny", RETURN_WINDOW_EXPIRED, order.order_id)
    return Verdict(
        "approve_refund",
        REFUND_APPROVED_IN_WINDOW,
        order.order_id,
        refund_amount=order.price_paid,
    )


def escalate_if_disputed(verdict: Verdict, prior_denials: int) -> Verdict:
    """Repeating a denied request escalates it; the verdict never flips."""
    if verdict.decision == "deny" and prior_denials >= active_policy()[
        "DENIALS_BEFORE_ESCALATION"
    ]:
        return Verdict("escalate", ESCALATED_POLICY_DISPUTE, verdict.order_id)
    return verdict


def returnable_now(
    orders: list, customer_id: str, today: date
) -> list:
    """The orders a return could actually be granted on.

    Offering a customer a book and then refusing it is the confidently
    unhelpful move: it costs them a turn to be told no. So the clarifying
    question asks about what this module would say yes to, which is why this
    filters through `decide_return` rather than applying a rule of its own.
    There is no new threshold here and there must not be one — a second
    definition of "returnable" would be a second place the answer could
    change.

    An order outside the window is still perfectly reachable: the customer
    names it, and gets a denial with a reason code, exactly as before. What
    changes is only what the agent volunteers.
    """
    return [
        order
        for order in orders
        if decide_return(order, customer_id, today).decision == "approve_refund"
    ]


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
    return attempts >= active_policy()["MAX_CLARIFY_ATTEMPTS"]


# A single out-of-scope request is answered honestly and offered a person; the
# escalation is for the customer who keeps asking for things the agent cannot
# do. A code constant, not an authorable parameter: it governs when a
# conversation reaches a human, and this branch keeps it in code deliberately.
UNHANDLED_BEFORE_ESCALATION = 1


def unhandled_limit_reached(streak: int) -> bool:
    """A repeated out-of-scope request is a customer the agent cannot help, and
    that belongs with a person. One honest decline first; the next escalates."""
    return streak >= UNHANDLED_BEFORE_ESCALATION


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


def _constants() -> tuple:
    """The descriptive constant surface, built live from the active policy so
    the interface shows the value in force now rather than an import-time copy.
    Exposed as `policy.CONSTANTS` through the module `__getattr__`."""
    now = active_policy()
    return tuple(
        Constant(parameter.name, now[parameter.name], parameter.why)
        for parameter in PARAMETERS
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
    ReasonCode(
        ESCALATED_UNHANDLED, (),
        "the agent, on a repeated out-of-scope request",
        "The customer asked for something the agent does not cover, more than "
        "once; a human picks it up rather than the agent guessing.",
    ),
)

_REASON_CODES_BY_CODE = {entry.code: entry for entry in REASON_CODES}


def constants_for(reason_code: str) -> list:
    """The named constants a verdict's reason code rests on, so a decision on
    screen can be traced to the line of policy that produced it — at the value
    in force now, read from the active policy rather than an import-time copy."""
    entry = _REASON_CODES_BY_CODE.get(reason_code)
    if entry is None:
        return []
    now = active_policy()
    return [
        {"name": name, "value": now[name], "why": _PARAMS_BY_NAME[name].why}
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


def __getattr__(name: str):  # PEP 562, supported on 3.7+
    """Resolve the authorable thresholds and the CONSTANTS surface from the
    active policy document.

    `policy.RETURN_WINDOW_DAYS` and the other two names are no longer literals
    on this module; they are read here from whatever the active document says,
    so every reader — agent.py, web.py, the checks — sees the value in force now,
    and a decision reads a threshold exactly as it always did. The number moved
    into a document; the place a verdict is computed did not. Anything else falls
    through to the normal AttributeError.
    """
    if name in POLICY_DEFAULTS:
        return active_policy()[name]
    if name == "CONSTANTS":
        return _constants()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
