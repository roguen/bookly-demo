"""The human review queue: where escalations land and a human resolves them.

NOTE ON THE FILENAME. This module shadows the standard library's `queue` for
anything running with the repo root on sys.path. Everything this build uses —
http.server, socketserver, threading, subprocess — is unaffected and is
checked on 3.9 and 3.13+. `concurrent.futures.ThreadPoolExecutor` is *not*:
it imports `queue` and wants `SimpleQueue`. Nothing here uses it, and if
something ever needs it, rename this module rather than working around it.

The design is one rule: resolution is append only.

A human never edits the verdict. The original denial stays in the record
forever, exactly as `policy.py` computed it, and what the human did becomes a
separate, later event pointing at it. That is the difference between a system
that can be audited and one that can be quietly corrected — and it is the
reason a reviewer can be given override authority at all. If overriding
rewrote the decision, the record would only ever show the last opinion.

A resolution emits its own envelope, with its own idempotency key, its own
audit line, and an `actor` field naming the human rather than the agent. Both
the actor and a free-text justification are required, not optional: an
override with nobody's name on it and no reason attached is exactly the
artifact an auditor cannot use.

Nothing here flows back. The agent never reads this queue; resolutions travel
outward to the orchestration layer, never inward into the next verdict.
`back_office_returns_nothing_that_reaches_a_verdict` asserts that structurally.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import envelope

QUEUE_PATH_ENV_VAR = "BOOKLY_QUEUE_PATH"
DEFAULT_QUEUE_PATH = "queue.json"

# What a reviewer may do. Deliberately short: "override" grants what policy
# denied, "uphold" agrees with it. Both are recorded identically, because a
# reviewer who agrees is as much a part of the record as one who does not.
ACTIONS = ("override", "uphold")

CASE_OPEN = "open"
CASE_RESOLVED = "resolved"


def queue_path() -> str:
    return os.environ.get(QUEUE_PATH_ENV_VAR) or DEFAULT_QUEUE_PATH


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Case:
    """One escalation, and everything that has happened to it since.

    `envelope` is the escalation exactly as the agent emitted it and is never
    written to again. Everything a human does lands in `events`.
    """

    case_id: str
    opened_at: str
    envelope: dict
    conversation: List[dict] = field(default_factory=list)
    events: List[dict] = field(default_factory=list)
    # Who the customer was, what the order was, and what the reason code
    # means — snapshotted when the case opened rather than looked up when it
    # is read. A ticket should show what was true at the moment it was
    # raised; a record that silently re-reads the world is one you cannot
    # reason about six weeks later. The console assembles it, so this module
    # still knows nothing about the store.
    context: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        return (
            CASE_RESOLVED
            if any(e["kind"] == "resolution" for e in self.events)
            else CASE_OPEN
        )

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "status": self.status,
            "opened_at": self.opened_at,
            # The original verdict, still readable, still unchanged.
            "envelope": self.envelope,
            "reason_code": self.envelope.get("reason_code"),
            "order_id": self.envelope.get("order_id"),
            "conversation_id": self.envelope.get("conversation_id"),
            "conversation": self.conversation,
            "events": self.events,
            "context": self.context,
        }


class ReviewQueue:
    def __init__(
        self,
        path: Optional[str] = None,
        now: Callable[[], str] = _utc_now,
    ) -> None:
        # The clock is injected for the same reason policy takes `today`: a
        # record with a timestamp in it is untestable otherwise.
        self._now = now
        self._path = Path(path or queue_path())
        self._lock = threading.RLock()
        self._cases: Dict[str, Case] = {}
        self._mtime: Optional[float] = None
        self._load()

    # -- persistence -------------------------------------------------------

    def _refresh(self) -> None:
        """Pick up writes made by the other process.

        The console and the back office are deliberately two processes, and
        both of them touch this queue: the console lands cases, the back
        office resolves them. The file is the shared state, so whoever is
        about to read it checks whether the other one has written since.
        Crude, and correct for one reviewer at one desk — which is the whole
        deployment this demo claims.
        """
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            if self._mtime is not None:
                self._cases.clear()  # the file went away; so did the cases
                self._mtime = None
            return
        if mtime != self._mtime:
            self._cases.clear()
            self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except ValueError:
            return  # a corrupt demo file is not worth crashing a demo over
        self._mtime = self._path.stat().st_mtime
        for record in raw.get("cases", []):
            self._cases[record["case_id"]] = Case(
                case_id=record["case_id"],
                opened_at=record["opened_at"],
                envelope=record["envelope"],
                conversation=record.get("conversation", []),
                events=record.get("events", []),
                context=record.get("context", {}),
            )

    def _save(self) -> None:
        payload = {"cases": [case.as_dict() for case in self._cases.values()]}
        temporary = self._path.with_name(self._path.name + ".tmp")
        temporary.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        temporary.replace(self._path)  # never a half-written queue on disk
        self._mtime = self._path.stat().st_mtime

    # -- reads -------------------------------------------------------------

    def cases(self) -> List[dict]:
        with self._lock:
            self._refresh()
            return [
                case.as_dict()
                for case in sorted(
                    self._cases.values(), key=lambda c: c.opened_at, reverse=True
                )
            ]

    def get(self, case_id: str) -> Optional[dict]:
        with self._lock:
            self._refresh()
            case = self._cases.get(case_id)
            return case.as_dict() if case else None

    def counts(self) -> dict:
        with self._lock:
            self._refresh()
            statuses = [case.status for case in self._cases.values()]
        return {
            "open": statuses.count(CASE_OPEN),
            "resolved": statuses.count(CASE_RESOLVED),
            "total": len(statuses),
        }

    # -- writes ------------------------------------------------------------

    def open_case(
        self,
        escalation: dict,
        conversation: Optional[List[dict]] = None,
        context: Optional[dict] = None,
    ) -> dict:
        """Land an escalation envelope as a case.

        Keyed on the envelope's own idempotency key, so a customer who presses
        the same denied request four times produces one case with four
        recorded pushes — the same contract the envelope already promises
        downstream, applied here.
        """
        case_id = _case_id(escalation)
        with self._lock:
            self._refresh()
            case = self._cases.get(case_id)
            if case is None:
                case = Case(
                    case_id=case_id,
                    opened_at=self._now(),
                    envelope=escalation,
                    conversation=list(conversation or []),
                    context=dict(context or {}),
                )
                case.events.append(
                    {
                        "sequence": 1,
                        "kind": "opened",
                        "at": case.opened_at,
                        "actor": "agent",
                        "reason_code": escalation.get("reason_code"),
                    }
                )
                self._cases[case_id] = case
            else:
                case.conversation = list(conversation or case.conversation)
                # The customer pushed again, so the background moved on. The
                # snapshot of who and what does not: that is the state the
                # case was raised against.
                case.events.append(
                    {
                        "sequence": len(case.events) + 1,
                        "kind": "escalation_repeated",
                        "at": self._now(),
                        "actor": "agent",
                        "reason_code": escalation.get("reason_code"),
                        "note": "same idempotency key; one case, not two",
                    }
                )
            self._save()
            return case.as_dict()

    def resolve(
        self, case_id: str, action: str, actor: str, justification: str
    ) -> dict:
        """Record what a human decided. Appends; never edits.

        Returns the case and the envelope the resolution emitted. Raises
        ValueError on anything missing, because a resolution with no name and
        no reason on it is not a resolution.
        """
        actor = (actor or "").strip()
        justification = (justification or "").strip()
        if action not in ACTIONS:
            raise ValueError(
                "action must be one of %s" % ", ".join(ACTIONS)
            )
        if not actor:
            raise ValueError("actor is required: a resolution names a person")
        if not justification:
            raise ValueError(
                "justification is required: an override with no reason "
                "attached is not reviewable"
            )
        with self._lock:
            self._refresh()
            case = self._cases.get(case_id)
            if case is None:
                raise ValueError("no such case: %s" % case_id)
            sequence = len(case.events) + 1
            # One resolution, one key. The ordinal is part of the material, so
            # re-delivering this resolution collapses downstream while a
            # genuinely later resolution is its own write.
            resolution_id = "%s#%d" % (case_id, sequence)
            emitted, delivery = envelope.emit_resolution(
                case_id=case_id,
                resolution_id=resolution_id,
                resolution=action,
                actor=actor,
                justification=justification,
                order_id=case.envelope.get("order_id"),
                conversation_id=case.envelope.get("conversation_id"),
                supersedes=case.envelope.get("idempotency_key"),
            )
            event = {
                "sequence": sequence,
                "kind": "resolution",
                "at": self._now(),
                "actor": actor,
                "action": action,
                "justification": justification,
                "envelope": emitted,
                "delivery": delivery,
                # Named so nobody reading this record can mistake it for an
                # edit of the original.
                "note": "appended; the original verdict above is unchanged",
            }
            case.events.append(event)
            self._save()
            return {"case": case.as_dict(), "event": event}

    def clear(self) -> None:
        """Back to a known state. Only the reset endpoint calls this."""
        with self._lock:
            self._cases.clear()
            self._mtime = None
            if self._path.exists():
                self._path.unlink()


def _case_id(escalation: dict) -> str:
    key = escalation.get("idempotency_key") or escalation.get("envelope_id") or ""
    return "case-%s" % key[:12]
