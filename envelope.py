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
import urllib.error
import urllib.request
import uuid
from typing import Optional, Tuple

AUDIT_PATH = "audit.log"
AUDIT_PATH_ENV_VAR = "BOOKLY_AUDIT_PATH"
WEBHOOK_ENV_VAR = "BOOKLY_WEBHOOK_URL"
DELIVERY_TIMEOUT_SECONDS = 3


def audit_path() -> str:
    """Where the audit line goes. Overridable so the console can run the
    check suite in a subprocess without fifteen test envelopes landing in
    the trail the demo is about to show on screen. Read per call, not at
    import, so a test can redirect it and put it back."""
    return os.environ.get(AUDIT_PATH_ENV_VAR) or AUDIT_PATH


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
    except urllib.error.URLError as error:
        return "failed_%s" % getattr(error, "reason", "unreachable")


def _audit(record: dict) -> None:
    with open(audit_path(), "a", encoding="utf-8") as audit_file:
        audit_file.write(json.dumps(record) + "\n")
