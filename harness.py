"""Golden transcripts: a scenario is a file, not a function.

`tests.py` pinned one conversation end to end with exact strings pasted into
the body of a check. That was the right idea in the wrong container: adding a
second scenario meant writing a second function and pasting more strings into
it, so the cost of coverage grew with every scenario and nobody was ever going
to pay it twice.

Here a scenario is a JSON file under `transcripts/`. This module loads it,
replays it through the same `Agent.handle_turn` the CLI and the console call,
and compares what happened to what the file says should happen. `tests.py`
turns every fixture into one generated check, so `python3 tests.py` remains the
single entry point and the console's Checks panel lists them by name.

What a fixture pins, and why each one:

  reply        the customer-visible text, verbatim. Wording drift should fail
               loudly — that is what a golden transcript is for.
  envelopes    the decision fields, including the literal idempotency key.
  stages       the sequence of recorder stages. This is the architectural
               claim as a regression test: a change that moved a decision to
               the model side of the boundary fails here. The *side* of each
               stage is deliberately not in the fixture — it is derived from
               recorder.STAGE_SIDES, so there is no second copy to drift.
  narrations   which event kinds the model was asked to phrase.

Replay is hermetic. BOOKLY_WEBHOOK_URL is removed for the duration, so a
golden transcript never depends on whether a receiver happened to be running,
and a check run never posts test envelopes into the ledger a demo is about to
show. Delivery is therefore always `skipped_no_url`, and the fixture records
it anyway: a transcript should be a complete record of the turn rather than a
selective one.

Nothing here decides anything. It replays, observes, and compares.
"""
from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent import Agent
from llm import RulesProvider
from recorder import ListRecorder

TRANSCRIPT_DIR = pathlib.Path(__file__).resolve().parent / "transcripts"
TRANSCRIPT_GLOB = "*.json"

# The envelope fields that are decisions. `envelope_id` is a uuid and is
# excluded because it is deliberately different every time; `customer_note` is
# excluded because it is the turn's own text, which the fixture already holds
# one field away — a second copy would be a thing that could drift. It is
# asserted against `customer` instead.
ENVELOPE_FIELDS = (
    "action",
    "order_id",
    "amount",
    "currency",
    "reason_code",
    "idempotency_key",
)

WEBHOOK_ENV_VAR = "BOOKLY_WEBHOOK_URL"


# ---------------------------------------------------------------------------
# What a fixture is.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    """One expected exchange. `reply` is None in a fixture that has been
    written but not yet blessed — the authoring path."""

    customer: str
    reply: Optional[str] = None
    stages: Optional[Tuple[str, ...]] = None
    narrations: Optional[Tuple[str, ...]] = None
    envelopes: Optional[Tuple[dict, ...]] = None

    @property
    def blessed(self) -> bool:
        return self.reply is not None


@dataclass(frozen=True)
class Transcript:
    id: str
    title: str
    why: str
    conversation_id: str
    turns: Tuple[Turn, ...]
    path: pathlib.Path

    @property
    def blessed(self) -> bool:
        return all(turn.blessed for turn in self.turns)


@dataclass(frozen=True)
class Observed:
    """What actually happened in one replayed turn."""

    customer: str
    reply: str
    stages: Tuple[str, ...]
    narrations: Tuple[str, ...]
    envelopes: Tuple[dict, ...]
    customer_notes: Tuple[Optional[str], ...]
    # (kind, facts, text) for each narration, kept for the rubric. The harness
    # itself never reads these: grading prose is a separate concern with a
    # separate module, and this is only the wire between them.
    narration_events: Tuple[Tuple[str, dict, str], ...] = ()

    def as_fixture_turn(self) -> dict:
        """The shape `--bless` writes back into the file."""
        return {
            "customer": self.customer,
            "reply": self.reply,
            "stages": list(self.stages),
            "narrations": list(self.narrations),
            "envelopes": [dict(e) for e in self.envelopes],
        }


# ---------------------------------------------------------------------------
# Loading.
# ---------------------------------------------------------------------------


def load(path: pathlib.Path) -> Transcript:
    raw = json.loads(path.read_text(encoding="utf-8"))
    turns = []
    for record in raw["turns"]:
        turns.append(
            Turn(
                customer=record["customer"],
                reply=record.get("reply"),
                stages=(
                    tuple(record["stages"]) if "stages" in record else None
                ),
                narrations=(
                    tuple(record["narrations"])
                    if "narrations" in record
                    else None
                ),
                envelopes=(
                    tuple(record["envelopes"])
                    if "envelopes" in record
                    else None
                ),
            )
        )
    return Transcript(
        id=raw["id"],
        title=raw["title"],
        why=raw["why"],
        conversation_id=raw["conversation_id"],
        turns=tuple(turns),
        path=path,
    )


def load_all(directory: Optional[pathlib.Path] = None) -> List[Transcript]:
    """Every fixture, in a stable order so check names and output do not
    depend on how the filesystem feels today."""
    directory = directory or TRANSCRIPT_DIR
    if not directory.exists():
        return []
    return [load(path) for path in sorted(directory.glob(TRANSCRIPT_GLOB))]


# ---------------------------------------------------------------------------
# Replay.
# ---------------------------------------------------------------------------


def replay(
    transcript: Transcript, provider: Optional[object] = None
) -> List[Observed]:
    """Drive the fixture's turns through a real Agent and report what
    happened. The same entry point the CLI and the console use — a harness
    that replayed through a shortcut would be testing the shortcut."""
    provider = provider if provider is not None else RulesProvider()
    saved = os.environ.pop(WEBHOOK_ENV_VAR, None)
    try:
        recorder = ListRecorder()
        agent = Agent(provider, transcript.conversation_id, recorder=recorder)
        observed = []
        for turn in transcript.turns:
            before = len(recorder.notes)
            result = agent.handle_turn(turn.customer)
            notes = recorder.notes[before:]
            observed.append(
                Observed(
                    customer=turn.customer,
                    reply=result.reply,
                    stages=tuple(note.stage for note in notes),
                    narrations=tuple(
                        note.payload["event"]
                        for note in notes
                        if note.stage == "narrate"
                    ),
                    envelopes=tuple(
                        _envelope_facts(emitted, delivery)
                        for emitted, delivery in result.envelopes
                    ),
                    customer_notes=tuple(
                        emitted.get("customer_note")
                        for emitted, _delivery in result.envelopes
                    ),
                    narration_events=tuple(
                        (
                            note.payload["event"],
                            note.payload["facts"],
                            note.payload["text"],
                        )
                        for note in notes
                        if note.stage == "narrate"
                    ),
                )
            )
        return observed
    finally:
        if saved is not None:
            os.environ[WEBHOOK_ENV_VAR] = saved


def _envelope_facts(emitted: dict, delivery: str) -> dict:
    facts = {field: emitted.get(field) for field in ENVELOPE_FIELDS}
    facts["delivery"] = delivery
    return facts


# ---------------------------------------------------------------------------
# Comparison.
# ---------------------------------------------------------------------------


def compare(
    transcript: Transcript,
    observed: Sequence[Observed],
    prose: bool = True,
) -> List[str]:
    """Every way the run differs from the fixture, as readable lines.

    Returns failures rather than raising, because the caller decides what a
    failure means: `tests.py` asserts on them, a hosted run reports them.

    `prose=False` skips the verbatim reply comparison and nothing else. A
    hosted model is expected to word things differently — that is the whole
    provider-parity claim — so on a hosted run the decisions are still pinned
    exactly and the prose is graded by the rubric instead. Skipping it is a
    decision, not an omission, and the caller has to ask for it.
    """
    failures: List[str] = []
    if len(observed) != len(transcript.turns):
        return [
            "%s: expected %d turns, replayed %d"
            % (transcript.id, len(transcript.turns), len(observed))
        ]
    for number, (expected, actual) in enumerate(
        zip(transcript.turns, observed), start=1
    ):
        where = "%s turn %d" % (transcript.id, number)
        if not expected.blessed:
            failures.append(
                "%s: fixture has no expected reply — run "
                "`python3 harness.py --bless %s`" % (where, transcript.id)
            )
            continue
        if prose and actual.reply != expected.reply:
            failures.append(
                "%s reply:\n    expected: %r\n    actual:   %r"
                % (where, expected.reply, actual.reply)
            )
        if expected.stages is not None and actual.stages != expected.stages:
            failures.append(
                "%s stages:\n    expected: %s\n    actual:   %s"
                % (where, list(expected.stages), list(actual.stages))
            )
        if (
            expected.narrations is not None
            and actual.narrations != expected.narrations
        ):
            failures.append(
                "%s narrations:\n    expected: %s\n    actual:   %s"
                % (where, list(expected.narrations), list(actual.narrations))
            )
        failures.extend(_compare_envelopes(where, expected, actual))
        # The envelope carries the customer's turn verbatim for the human
        # reading the audit trail. Asserted against the turn rather than
        # stored again, so an injected sentence has to survive intact.
        for note in actual.customer_notes:
            if note != expected.customer:
                failures.append(
                    "%s customer_note:\n    expected: %r\n    actual:   %r"
                    % (where, expected.customer, note)
                )
    return failures


def _compare_envelopes(
    where: str, expected: Turn, actual: Observed
) -> List[str]:
    if expected.envelopes is None:
        return []
    failures = []
    if len(actual.envelopes) != len(expected.envelopes):
        return [
            "%s: expected %d envelope(s), got %d — %s"
            % (
                where,
                len(expected.envelopes),
                len(actual.envelopes),
                [e.get("action") for e in actual.envelopes],
            )
        ]
    for index, (want, got) in enumerate(
        zip(expected.envelopes, actual.envelopes)
    ):
        for field in tuple(ENVELOPE_FIELDS) + ("delivery",):
            if want.get(field) != got.get(field):
                failures.append(
                    "%s envelope %d %s:\n    expected: %r\n    actual:   %r"
                    % (where, index + 1, field, want.get(field), got.get(field))
                )
    return failures


# ---------------------------------------------------------------------------
# Blessing.
# ---------------------------------------------------------------------------


def bless(transcript: Transcript, observed: Sequence[Observed]) -> None:
    """Rewrite a fixture from a real run.

    This is how a fixture is authored: write the file with an id, a title, a
    why, a conversation id and the customer turns, then bless it to fill in
    what the agent actually did.

    It is also, unavoidably, how a regression gets enshrined — blessing is one
    command and a wrong expectation looks exactly like a right one afterwards.
    There is no way to make that safe inside the tool, so the mitigation lives
    outside it: the diff is the review. That is one of the reasons the commit
    history in this repo is meant to be read.
    """
    payload = {
        "id": transcript.id,
        "title": transcript.title,
        "why": transcript.why,
        "conversation_id": transcript.conversation_id,
        "turns": [turn.as_fixture_turn() for turn in observed],
    }
    transcript.path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Running the whole set.
# ---------------------------------------------------------------------------


@dataclass
class Result:
    transcript: Transcript
    failures: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def run_all(
    provider: Optional[object] = None,
    prose: bool = True,
    directory: Optional[pathlib.Path] = None,
) -> List[Result]:
    results = []
    for transcript in load_all(directory):
        observed = replay(transcript, provider)
        results.append(Result(transcript, compare(transcript, observed, prose)))
    return results


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Replay the golden transcripts under transcripts/."
    )
    parser.add_argument(
        "--bless",
        metavar="ID",
        help="rewrite one fixture (or 'all') from a real run",
    )
    arguments = parser.parse_args(argv)

    transcripts = load_all()
    if not transcripts:
        print("no transcripts found in %s" % TRANSCRIPT_DIR)
        return 1

    if arguments.bless:
        wanted = [
            t for t in transcripts
            if arguments.bless in ("all", t.id)
        ]
        if not wanted:
            print("no transcript with id %r" % arguments.bless)
            return 1
        for transcript in wanted:
            bless(transcript, replay(transcript))
            print("blessed %s (%s)" % (transcript.id, transcript.path.name))
        print("\nreview the diff before committing: a blessed regression "
              "looks exactly like a blessed fix.")
        return 0

    failures = 0
    for result in run_all():
        if result.ok:
            print("ok    %s" % result.transcript.id)
        else:
            failures += 1
            print("FAIL  %s" % result.transcript.id)
            for line in result.failures:
                print("      %s" % line.replace("\n", "\n      "))
    print("\n%d passed, %d failed" % (len(transcripts) - failures, failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
