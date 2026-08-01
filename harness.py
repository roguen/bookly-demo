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

import rubric
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

# Golden transcripts decide on the historical policy, never on a document a demo
# authored — the same hermeticity the webhook gets below. Every harness run,
# replay or bless, is pointed at an absent policy document.
os.environ["BOOKLY_POLICY_PATH"] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "policy.checks.json"
)
# Replay never delivers (the webhook is unset below), so it never enqueues; the
# outbox and dead-letter are pinned to absent paths anyway for hermeticity.
for _var, _name in (
    ("BOOKLY_OUTBOX_PATH", "outbox.checks.json"),
    ("BOOKLY_DEADLETTER_PATH", "dead_letter.checks.json"),
):
    os.environ[_var] = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), _name
    )


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
    # Rubric findings this fixture knowingly still produces, each pointing at
    # the issue that will remove it. See _compare_rubric for why an
    # acknowledgement is a different thing from a suppression.
    known_gaps: Tuple[dict, ...] = ()
    # Findings that are correct behaviour here and will never be fixed,
    # because the rubric's rule is a heuristic and this is a case where the
    # heuristic is wrong. Kept separate from known_gaps on purpose: one is a
    # debt with an issue number, the other is a design decision with a reason,
    # and collapsing them would let a defect hide in the wrong list.
    accepted: Tuple[dict, ...] = ()

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
        known_gaps=tuple(raw.get("known_gaps", ())),
        accepted=tuple(raw.get("accepted", ())),
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
    gate_rubric: bool = True,
) -> List[str]:
    """Every way the run differs from the fixture, as readable lines.

    Returns failures rather than raising, because the caller decides what a
    failure means: `tests.py` asserts on them, a hosted run reports them.

    `prose=False` skips the verbatim reply comparison and nothing else. A
    hosted model is expected to word things differently — that is the whole
    provider-parity claim — so on a hosted run the decisions are still pinned
    exactly and the prose is graded by the rubric instead. Skipping it is a
    decision, not an omission, and the caller has to ask for it.

    `gate_rubric=False` reports rubric findings rather than failing on them.
    A fixture's `known_gaps` are statements about what the *stand-in* still
    gets wrong; holding a different narrator to them would be comparing a
    hosted model against a list of somebody else's defects. On a hosted run
    the findings are the report, and only the decisions are a gate.
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
    if gate_rubric:
        failures.extend(_compare_rubric(transcript, observed))
    return failures


def findings_for(observed: Sequence[Observed]) -> List[rubric.Finding]:
    """The rubric's read on a replayed conversation, ungated. What a hosted
    run reports instead of gating on."""
    return rubric.grade(
        [(turn.reply, turn.narration_events) for turn in observed]
    )


def _compare_rubric(
    transcript: Transcript, observed: Sequence[Observed]
) -> List[str]:
    """Grade the prose, and hold the fixture to what it admits to.

    The rubric runs on every provider, because prose is the thing that is not
    otherwise pinned. Its findings are compared against `known_gaps` — an
    explicit list of defects this fixture still produces, each carrying the
    issue number that will close it.

    An acknowledgement is not a suppression, and the difference is enforced
    in both directions:

      * a finding nobody acknowledged fails, so a new defect cannot hide
        behind an old one — acknowledgements carry a count, not just a rule
        name, and a second occurrence of an acknowledged rule is a new defect.
      * an acknowledgement with no matching finding fails, so a gap that was
        fixed cannot leave its excuse behind. The fix has to delete the
        admission, which is what stops this list from becoming the place
        failures go to be forgotten.
      * an acknowledgement with no issue number and no reason fails, because
        that is a suppression wearing an acknowledgement's clothes.
    """
    findings = rubric.grade(
        [(turn.reply, turn.narration_events) for turn in observed]
    )
    counted: Dict[str, int] = {}
    for finding in findings:
        counted[finding.rule] = counted.get(finding.rule, 0) + 1

    failures = []
    allowed: Dict[str, int] = {}
    for gap in transcript.known_gaps:
        rule = gap.get("rule")
        if not gap.get("issue") or not gap.get("why"):
            failures.append(
                "%s known_gap %r has no issue number or no reason, which "
                "makes it a suppression rather than an acknowledgement"
                % (transcript.id, rule)
            )
            continue
        allowed[rule] = allowed.get(rule, 0) + int(gap.get("occurrences", 1))
    for entry in transcript.accepted:
        rule = entry.get("rule")
        if not entry.get("why"):
            failures.append(
                "%s accepted %r has no reason. An accepted finding is a "
                "design decision, and a design decision with no argument "
                "behind it is a suppression" % (transcript.id, rule)
            )
            continue
        allowed[rule] = allowed.get(rule, 0) + int(entry.get("occurrences", 1))

    for rule, seen in sorted(counted.items()):
        if seen > allowed.get(rule, 0):
            for finding in findings:
                if finding.rule == rule:
                    failures.append("%s rubric %s" % (transcript.id, finding))
    for rule, expected_count in sorted(allowed.items()):
        if counted.get(rule, 0) < expected_count:
            failures.append(
                "%s %r is stale: the rubric no longer reports it, so the "
                "entry in known_gaps or accepted should be deleted"
                % (transcript.id, rule)
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
    # Blessing rewrites what the agent did. It deliberately does not touch
    # what the fixture admits to still getting wrong: an acknowledgement is a
    # human's statement about an open defect, and regenerating it from a run
    # would let a re-bless quietly launder one.
    if transcript.known_gaps:
        payload["known_gaps"] = [dict(gap) for gap in transcript.known_gaps]
    if transcript.accepted:
        payload["accepted"] = [dict(entry) for entry in transcript.accepted]
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
    # Every rubric finding, gated or not. On a stand-in run the gated ones are
    # already in `failures`; on a hosted run this is the report itself.
    findings: List[rubric.Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def run_all(
    provider: Optional[object] = None,
    prose: bool = True,
    gate_rubric: bool = True,
    directory: Optional[pathlib.Path] = None,
) -> List["Result"]:
    results = []
    for transcript in load_all(directory):
        observed = replay(transcript, provider)
        results.append(
            Result(
                transcript,
                compare(transcript, observed, prose, gate_rubric),
                findings_for(observed),
            )
        )
    return results


# The default path is the stand-in, and stays the default path. A hosted run
# is something a person asks for, at a terminal, once — it costs money and it
# needs a network, and neither belongs in `python3 tests.py`.
DEFAULT_PROVIDER = "rules"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    import llm

    parser = argparse.ArgumentParser(
        description="Replay the golden transcripts under transcripts/.",
        epilog="tests.py only ever runs the stand-in, so the suite stays "
               "offline, dependency-free and free.",
    )
    parser.add_argument(
        "--bless",
        metavar="ID",
        help="rewrite one fixture (or 'all') from a real run",
    )
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        choices=sorted(llm.PROVIDERS),
        help="which narrator to replay through (default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="also write the report here, for evidence/",
    )
    arguments = parser.parse_args(argv)

    transcripts = load_all()
    if not transcripts:
        print("no transcripts found in %s" % TRANSCRIPT_DIR)
        return 1

    if arguments.bless:
        if arguments.provider != DEFAULT_PROVIDER:
            # A fixture blessed from a hosted run would pin one sampling of
            # one model's prose as the expected text of the repo, which is
            # the opposite of what these files are for.
            print("refusing to bless from %r: fixtures are blessed from the "
                  "stand-in, which is the deterministic one."
                  % arguments.provider)
            return 1
        wanted = [t for t in transcripts if arguments.bless in ("all", t.id)]
        if not wanted:
            print("no transcript with id %r" % arguments.bless)
            return 1
        for transcript in wanted:
            bless(transcript, replay(transcript))
            print("blessed %s (%s)" % (transcript.id, transcript.path.name))
        print("\nreview the diff before committing: a blessed regression "
              "looks exactly like a blessed fix.")
        return 0

    hosted = arguments.provider != DEFAULT_PROVIDER
    try:
        provider = llm.build_provider(arguments.provider)
        # Constructing a client is offline for both vendors, so a missing key,
        # a renamed model and a missing network all look like success until
        # the first turn — which here means a stack trace halfway through a
        # replay. The console already solved this at its provider button;
        # the same one small call solves it here.
        if isinstance(provider, llm.HostedProvider):
            provider.verify()
    except Exception as error:
        text = " ".join(str(error).split())
        print("could not start %s: %s: %s"
              % (arguments.provider, type(error).__name__, text[:240]))
        print("set the vendor key in the environment and try again.")
        return 1

    results = run_all(provider, prose=not hosted, gate_rubric=not hosted)
    report = _report(results, arguments.provider, hosted)
    print(report)
    if arguments.out:
        pathlib.Path(arguments.out).write_text(report + "\n", encoding="utf-8")
        print("\nwritten to %s" % arguments.out)
    return 1 if any(not r.ok for r in results) else 0


def _report(results: Sequence["Result"], provider: str, hosted: bool) -> str:
    lines = [
        "Golden transcripts, narrator: %s" % provider,
        "",
    ]
    if hosted:
        lines += [
            "Hosted run. The decision layer is compared exactly — every",
            "envelope field, every reason code, every idempotency key, the",
            "same as on the stand-in. The verbatim reply comparison is",
            "deliberately skipped: a hosted model is expected to word things",
            "differently, and that is the parity claim rather than a defect.",
            "The prose is graded by the rubric instead, and the findings",
            "below are the report rather than a gate — a fixture's known_gaps",
            "describe what the stand-in gets wrong, and holding another",
            "narrator to that list would be comparing it against somebody",
            "else's defects.",
            "",
        ]
    else:
        lines += [
            "Stand-in run. Replies are compared verbatim and the rubric gates:",
            "any finding a fixture has not acknowledged in known_gaps fails.",
            "",
        ]
    failed = 0
    for result in results:
        if result.ok:
            lines.append("ok    %s" % result.transcript.id)
        else:
            failed += 1
            lines.append("FAIL  %s" % result.transcript.id)
            for line in result.failures:
                lines.append("      %s" % line.replace("\n", "\n      "))
    lines.append("")
    lines.append("%d passed, %d failed" % (len(results) - failed, failed))

    lines.append("")
    lines.append("NARRATION RUBRIC")
    total = 0
    for result in results:
        # Which findings this fixture already admits to, so the report
        # distinguishes a known open defect from a new one rather than
        # printing a wall of undifferentiated lines.
        marks = {
            gap.get("rule"): " (known, #%s)" % gap.get("issue")
            for gap in result.transcript.known_gaps
        }
        marks.update({
            entry.get("rule"): " (accepted)"
            for entry in result.transcript.accepted
        })
        for finding in result.findings:
            total += 1
            lines.append(
                "  %s%s %s"
                % (result.transcript.id, marks.get(finding.rule, ""), finding)
            )
    if not total:
        lines.append("  no findings.")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
