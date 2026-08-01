"""The boundary between deciding and executing.

The agent never calls a refund API. It emits an action envelope; whatever
orchestration layer the customer already runs owns delivery, retries, and
the actual write. The idempotency key is derived from stable facts of the
decision, so the same decision can be emitted, retried, or replayed and the
downstream system still posts it exactly once. The audit line is written
before the network hop: if delivery fails, the record of the decision
survives.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable, List, Optional, Tuple

AUDIT_PATH = "audit.log"
AUDIT_PATH_ENV_VAR = "BOOKLY_AUDIT_PATH"
WEBHOOK_ENV_VAR = "BOOKLY_WEBHOOK_URL"
DELIVERY_TIMEOUT_SECONDS = 3

# --- the outbox: a failed delivery is not lost, it waits to be retried ------
#
# emit() still audits the decision first and makes one fast attempt, so the
# turn never blocks. A *failed* attempt does not vanish: the envelope is
# appended to a durable outbox, and reconcile() re-delivers it later with
# bounded exponential backoff, moving one that exhausts its attempts to a
# dead-letter store for a human. This is the executor's job, on the executor's
# side of the boundary — the agent still emits and never branches on delivery.
OUTBOX_PATH_ENV_VAR = "BOOKLY_OUTBOX_PATH"
DEADLETTER_PATH_ENV_VAR = "BOOKLY_DEADLETTER_PATH"
OUTBOX_PATH = "outbox.json"
DEADLETTER_PATH = "dead_letter.json"
# emit() is the first attempt; reconcile() makes the rest, so this is the total
# across both before an envelope is dead-lettered.
MAX_DELIVERY_ATTEMPTS = 5
# Exponential, capped. Enforced by a not-before timestamp compared against the
# clock reconcile is given — never by sleeping, so a check can step through the
# backoff with an injected clock and the console never blocks.
BACKOFF_BASE_SECONDS = 2
BACKOFF_CAP_SECONDS = 300

_store_lock = threading.RLock()


def audit_path() -> str:
    """Where the audit line goes. Overridable so the console can run the
    check suite in a subprocess without fifteen test envelopes landing in
    the trail the demo is about to show on screen. Read per call, not at
    import, so a test can redirect it and put it back."""
    return os.environ.get(AUDIT_PATH_ENV_VAR) or AUDIT_PATH


def outbox_path() -> str:
    """Undelivered envelopes waiting to be retried. Read per call so a test or
    the console can redirect it, exactly like the audit trail."""
    return os.environ.get(OUTBOX_PATH_ENV_VAR) or OUTBOX_PATH


def deadletter_path() -> str:
    """Envelopes that exhausted their delivery attempts and need a human."""
    return os.environ.get(DEADLETTER_PATH_ENV_VAR) or DEADLETTER_PATH


def _read_json(path: str, default):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return data if isinstance(data, list) else default


def _write_json(path: str, data) -> None:
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(target)  # atomic on the same filesystem


def _backoff(attempts: int) -> float:
    return min(
        BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)), BACKOFF_CAP_SECONDS
    )


def idempotency_key(conversation_id: str, action: str, order_id: str) -> str:
    """One decision, one key. Retries and replays hash to the same value."""
    material = "%s|%s|%s" % (conversation_id, action, order_id)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def emit(
    action: str,
    conversation_id: str,
    order_id: Optional[str],
    reason_code: str,
    amount: Optional[float] = None,
    customer_note: Optional[str] = None,
) -> Tuple[dict, str]:
    """Build, audit, then attempt delivery — audited first, so the decision
    survives a failed hop.

    The customer_note is carried verbatim as inert metadata for the human
    reading the audit trail. Nothing parses it; the amount and reason code
    arrive already decided by policy. Returns the envelope and its delivery
    status, which is recorded for the audit line and never branched on.
    """
    envelope = {
        "envelope_id": uuid.uuid4().hex,
        # An escalation can precede order resolution; the key still needs a
        # stable third component.
        "idempotency_key": idempotency_key(
            conversation_id, action, order_id or "unresolved"
        ),
        "action": action,
        "order_id": order_id,
        "amount": amount,
        "currency": "USD" if amount is not None else None,
        "reason_code": reason_code,
        "conversation_id": conversation_id,
        "customer_note": customer_note,
    }
    _audit({"event": "emitted", "envelope": envelope})
    delivery = _deliver(envelope)
    _audit(
        {
            "event": "delivery",
            "envelope_id": envelope["envelope_id"],
            "delivery": delivery,
        }
    )
    # A failed hop is not lost. The decision is already audited; the envelope
    # now waits in the outbox for reconcile() to retry it. The delivery string
    # is still returned and still never branched on by the agent.
    if delivery.startswith("failed"):
        _enqueue_outbox(envelope, delivery)
    return envelope, delivery


def emit_resolution(
    case_id: str,
    resolution_id: str,
    resolution: str,
    actor: str,
    justification: str,
    order_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    supersedes: Optional[str] = None,
) -> Tuple[dict, str]:
    """A human's decision on an escalated case, emitted the same way.

    Deliberately a separate function rather than an `actor` argument on
    `emit`. Two reasons, and both matter more than the saved lines:

    The agent's envelopes are unchanged, byte for byte, which keeps the
    evidence in evidence/duplicate_receipt.txt valid against a receiver that
    prints whatever it is sent.

    And these are not the same kind of record. An agent envelope carries a
    reason code, because a policy function produced it. A resolution carries
    a person and a sentence, because a person produced it — inventing a
    reason code for a human judgement would be the dishonest field on the
    screen. `supersedes` points back at the decision under review without
    touching it: the original stays exactly as policy computed it.
    """
    envelope = {
        "envelope_id": uuid.uuid4().hex,
        "idempotency_key": idempotency_key(
            case_id, "resolve_case", resolution_id
        ),
        "action": "resolve_case",
        "resolution": resolution,
        "case_id": case_id,
        "order_id": order_id,
        "conversation_id": conversation_id,
        # Who, and why. Both required upstream in queue.py.
        "actor": actor,
        "justification": justification,
        "supersedes": supersedes,
    }
    _audit({"event": "emitted", "envelope": envelope})
    delivery = _deliver(envelope)
    _audit(
        {
            "event": "delivery",
            "envelope_id": envelope["envelope_id"],
            "delivery": delivery,
        }
    )
    # A failed hop is not lost. The decision is already audited; the envelope
    # now waits in the outbox for reconcile() to retry it. The delivery string
    # is still returned and still never branched on by the agent.
    if delivery.startswith("failed"):
        _enqueue_outbox(envelope, delivery)
    return envelope, delivery


def _deliver(envelope: dict) -> str:
    url = os.environ.get(WEBHOOK_ENV_VAR)
    if not url:
        return "skipped_no_url"
    body = json.dumps(envelope).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(
            request, timeout=DELIVERY_TIMEOUT_SECONDS
        ) as response:
            return "delivered_%d" % response.status
    except urllib.error.HTTPError as error:
        # The receiver answered and refused. Its status is the useful fact.
        return "failed_%d" % error.code
    except (urllib.error.URLError, socket.timeout, TimeoutError):
        # Nobody answered. Normalised to one stable value rather than the
        # platform's errno text, because this string is read off a screen
        # during the failure demo and "failed_[Errno 61] Connection refused"
        # is noise where "failed_unreachable" is the point: the decision was
        # already made and already audited, and only the hop was lost.
        return "failed_unreachable"


def _enqueue_outbox(envelope: dict, delivery: str) -> None:
    """Record an undelivered envelope so it is retried, not lost. Eligible for
    the very next reconcile — the backoff starts only after the first retry
    also fails, so `not_before` begins at 0."""
    with _store_lock:
        entries = _read_json(outbox_path(), [])
        entries.append(
            {
                "envelope": envelope,
                "attempts": 1,  # emit() was the first attempt
                "last_error": delivery,
                "not_before": 0.0,
            }
        )
        _write_json(outbox_path(), entries)
    _audit(
        {
            "event": "outbox_enqueued",
            "envelope_id": envelope["envelope_id"],
            "idempotency_key": envelope.get("idempotency_key"),
            "delivery": delivery,
        }
    )


def reconcile(
    now: Optional[float] = None,
    deliver: Optional[Callable[[dict], str]] = None,
) -> dict:
    """Retry the outbox. The executor draining its own backlog.

    Each eligible envelope is re-delivered once; on success it leaves the
    outbox, on failure its attempt count and its next-eligible time move out by
    the backoff, and once it has used all its attempts it moves to the
    dead-letter store for a human. Exactly-once is not this function's job —
    it may re-deliver an envelope the receiver already recorded — but the
    receiver dedups on the idempotency key, so a duplicate is suppressed rather
    than executed twice. Deterministic: the clock is injected, and nothing here
    sleeps.
    """
    now = time.time() if now is None else now
    deliver = deliver or _deliver
    delivered: List[str] = []
    dead_lettered: List[str] = []
    with _store_lock:
        entries = _read_json(outbox_path(), [])
        remaining: List[dict] = []
        for entry in entries:
            envelope = entry.get("envelope") or {}
            envelope_id = envelope.get("envelope_id")
            if now < entry.get("not_before", 0.0):
                remaining.append(entry)  # still backing off
                continue
            result = deliver(envelope)
            entry["attempts"] = entry.get("attempts", 1) + 1
            entry["last_error"] = result
            _audit(
                {
                    "event": "redelivery",
                    "envelope_id": envelope_id,
                    "idempotency_key": envelope.get("idempotency_key"),
                    "attempt": entry["attempts"],
                    "delivery": result,
                }
            )
            if not result.startswith("failed"):
                delivered.append(envelope_id)
            elif entry["attempts"] >= MAX_DELIVERY_ATTEMPTS:
                _append_deadletter(entry, result)
                dead_lettered.append(envelope_id)
            else:
                entry["not_before"] = now + _backoff(entry["attempts"])
                remaining.append(entry)
        _write_json(outbox_path(), remaining)
        pending = len(remaining)
    return {
        "delivered": delivered,
        "dead_lettered": dead_lettered,
        "pending": pending,
    }


def _append_deadletter(entry: dict, result: str) -> None:
    with _store_lock:
        dead = _read_json(deadletter_path(), [])
        dead.append(
            {
                "envelope": entry.get("envelope"),
                "attempts": entry.get("attempts"),
                "last_error": result,
            }
        )
        _write_json(deadletter_path(), dead)
    _audit(
        {
            "event": "dead_letter",
            "envelope_id": (entry.get("envelope") or {}).get("envelope_id"),
            "idempotency_key": (entry.get("envelope") or {}).get(
                "idempotency_key"
            ),
            "attempts": entry.get("attempts"),
        }
    )


def outbox() -> List[dict]:
    """The undelivered envelopes waiting, for a surface to show."""
    return _read_json(outbox_path(), [])


def dead_letters() -> List[dict]:
    """The envelopes that gave up, for a surface to show."""
    return _read_json(deadletter_path(), [])


def _audit(record: dict) -> None:
    with open(audit_path(), "a", encoding="utf-8") as audit_file:
        audit_file.write(json.dumps(record) + "\n")
