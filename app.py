"""The shell: a CLI chat loop and a scripted demo runner.

This file owns presentation only — reading input, printing turns, and
printing emitted envelopes. All behavior lives behind Agent.handle_turn.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from agent import Agent, TurnResult
from llm import Provider, make_provider

KEY_PREVIEW_CHARS = 8  # enough of the idempotency key to eyeball, no more


def print_turn_output(result: TurnResult) -> None:
    print("bookly> %s" % result.reply)
    for emitted, delivery in result.envelopes:
        print(
            "  [envelope %s] order=%s amount=%s reason=%s delivery=%s key=%s…"
            % (
                emitted["action"],
                emitted["order_id"],
                _fmt_amount(emitted["amount"]),
                emitted["reason_code"],
                delivery,
                emitted["idempotency_key"][:KEY_PREVIEW_CHARS],
            )
        )


def _fmt_amount(amount: Optional[float]) -> str:
    return "-" if amount is None else "$%.2f" % amount


def run_script(path: str, provider: Provider) -> None:
    """Replay a demo script: `---` starts a new conversation, `#` lines are
    echoed as scene headings, and blank lines are skipped."""
    conversation_number = 0
    agent = None
    with open(path, "r", encoding="utf-8") as script:
        for line in script:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                print("\n%s" % line)
                continue
            if line == "---":
                agent = None
                continue
            if agent is None:
                conversation_number += 1
                agent = Agent(provider, "conv-%d" % conversation_number)
                print("\n=== conversation conv-%d ===" % conversation_number)
            print("customer> %s" % line)
            print_turn_output(agent.handle_turn(line))


def run_repl(provider: Provider) -> None:
    print("Bookly support agent. Type 'exit' to quit.")
    agent = Agent(provider, "conv-live")
    while True:
        try:
            line = input("you> ").strip()
        except EOFError:
            break
        if line.lower() in ("exit", "quit"):
            break
        if not line:
            continue
        print_turn_output(agent.handle_turn(line))


def main() -> int:
    parser = argparse.ArgumentParser(description="Bookly support agent")
    parser.add_argument(
        "--script", help="replay a demo script instead of the live REPL"
    )
    args = parser.parse_args()
    try:
        provider = make_provider()
    except ValueError as error:  # an ambiguous or misspelled provider choice
        print("configuration error: %s" % error)
        return 2
    print("provider: %s" % provider.name)
    try:
        if args.script:
            run_script(args.script, provider)
        else:
            run_repl(provider)
    except Exception as error:
        # A hosted provider can fail for reasons that are not bugs — no
        # credits, a renamed model, no network. A stack trace mid-demo helps
        # nobody, so say what happened and how to get running again.
        print("\n%s failed: %s: %s" % (provider.name, type(error).__name__, error))
        print(
            "Run with BOOKLY_PROVIDER=rules to use the dependency-free "
            "stand-in, or check the vendor's credits and model name."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
