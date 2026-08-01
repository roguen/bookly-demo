"""Drain the delivery outbox: retry undelivered envelopes, dead-letter the
exhausted ones. The executor's reconcile loop, run by hand.

Point BOOKLY_WEBHOOK_URL at the receiver (the back office) and run this after it
comes back up:

    BOOKLY_WEBHOOK_URL=http://127.0.0.1:8787/webhook python3 reconcile.py

It re-delivers each pending envelope; the receiver dedups on the idempotency
key, so a re-delivery it already saw is suppressed rather than posted twice. It
runs one pass and prints a summary — a real deployment would run it on a
schedule, but by hand is what makes it something you can watch. It decides
nothing and executes nothing itself: it hands the same envelope back to the
receiver, which is the only thing that posts.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent
os.environ.setdefault("BOOKLY_AUDIT_PATH", str(REPO / "audit.log"))
os.environ.setdefault("BOOKLY_OUTBOX_PATH", str(REPO / "outbox.json"))
os.environ.setdefault("BOOKLY_DEADLETTER_PATH", str(REPO / "dead_letter.json"))

import envelope  # noqa: E402  (after the paths are pinned)


def main() -> None:
    pending = envelope.outbox()
    if not pending:
        print("outbox empty — nothing to reconcile.")
        return
    print("reconciling %d pending envelope(s)…" % len(pending))
    if not os.environ.get(envelope.WEBHOOK_ENV_VAR):
        print(
            "warning: %s is not set, so deliveries have nowhere to go and will "
            "count as failures." % envelope.WEBHOOK_ENV_VAR
        )
    result = envelope.reconcile()
    print("  delivered:     %d" % len(result["delivered"]))
    print("  dead-lettered: %d" % len(result["dead_lettered"]))
    print("  still pending: %d" % result["pending"])
    dead = envelope.dead_letters()
    if dead:
        print(
            "\n%d envelope(s) have exhausted their attempts and are in the "
            "dead-letter store for a human: %s"
            % (len(dead), envelope.deadletter_path())
        )


if __name__ == "__main__":
    main()
