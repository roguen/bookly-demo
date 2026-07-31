"""The shell: a CLI chat loop and a scripted demo runner.

This file owns presentation only — reading input, printing turns, and
printing emitted envelopes. All behavior lives behind Agent.handle_turn.
"""
from __future__ import annotations

import argparse
import sys

from agent import Agent, TurnResult
from llm import make_provider

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


def _fmt_amount(amount) -> str:
    return "-" if amount is None else "$%.2f" % amount


def run_script(path: str, provider) -> None:
    """Replay a demo script. `---` starts a new conversation; `#` comments."""
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


def run_repl(provider) -> None:
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
    provider = make_provider()
    print("provider: %s" % provider.name)
    if args.script:
        run_script(args.script, provider)
    else:
        run_repl(provider)
    return 0


if __name__ == "__main__":
    sys.exit(main())
