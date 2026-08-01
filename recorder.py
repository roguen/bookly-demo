"""What happened inside a turn, recorded without the agent presenting it.

The console has to show the inside of a turn — the slots, the lookups, the
verdict, the envelope. The obvious way to get that is to have `handle_turn`
assemble a payload for the UI, and it is the wrong way: it puts presentation
inside the orchestrator and makes the agent's behaviour depend on who is
watching.

So the agent talks to a recorder that does nothing. `NullRecorder.note` is an
empty method. The CLI passes no recorder and gets it, which is *why* the CLI
cannot change: there is no branch anywhere that asks whether anyone is
listening. The web layer passes a `ListRecorder` and collects the same calls.

Every note carries which side of the boundary produced it, and that mapping
lives here rather than at each call site — one table, reviewable at a glance,
and no way for a call site to mislabel itself. The interface colours by this
tag, so getting it right is the design decision, not a formatting one.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, is_dataclass
from typing import Any, Dict, List, Protocol

# The two sides of the boundary the whole build argues about.
MODEL = "model"
DETERMINISTIC = "deterministic"

# The customer's own words are neither side, which is why there is no third
# constant here and no note stage for them: the conversation column renders
# the customer turn, and the trace records only what the system did with it.

STAGE_SIDES = {
    # The model turned text into slots. Tagged model even when the rules
    # stand-in is doing it — the tag names the seat, not the implementation.
    # Colouring the regex purple would quietly claim it is trustworthy in a
    # way a hosted model is not, which is the opposite of the argument.
    "extract": MODEL,
    # The state machine choosing what to do next. Never what to grant.
    "route": DETERMINISTIC,
    # tools.py handing back records and articles. Facts, never prose.
    "lookup": DETERMINISTIC,
    # Which orders could take the write, and whether that is a real choice.
    "candidates": DETERMINISTIC,
    # Whether to ask, re-ask, or stop asking. The *wording* of the question
    # is a separate narrate note, because deciding to ask and phrasing the
    # ask are on opposite sides and must never render as one row.
    "clarify": DETERMINISTIC,
    # policy.py's complete answer.
    "verdict": DETERMINISTIC,
    # The action record, and how delivery went.
    "envelope": DETERMINISTIC,
    # The model phrasing a decision that was already made.
    "narrate": MODEL,
}

STAGES = frozenset(STAGE_SIDES)
UNKNOWN_SIDE = "unknown"


def side_of(stage: str) -> str:
    """A stage nobody declared is recorded as unknown rather than raised on.
    The recorder is a bystander; it must never be able to break a turn. A
    check asserts the agent's whole vocabulary is declared, so a typo fails
    loudly in the suite instead of quietly on stage."""
    return STAGE_SIDES.get(stage, UNKNOWN_SIDE)


@dataclass(frozen=True)
class Note:
    """One thing worth showing, already computed by the code that noted it."""

    sequence: int
    stage: str
    side: str
    payload: Dict[str, Any]

    def as_dict(self) -> dict:
        return {
            "sequence": self.sequence,
            "stage": self.stage,
            "side": self.side,
            "payload": self.payload,
        }


class Recorder(Protocol):
    def note(self, stage: str, payload: dict) -> None:
        ...


class NullRecorder:
    """Does nothing, on purpose, and is the default. The CLI's behaviour is
    identical to v1.0.0 because this is what it gets and this is all it is."""

    def note(self, stage: str, payload: dict) -> None:
        pass


# NullRecorder holds no state, so one shared instance is correct and the
# usual "no mutable default argument" caution does not apply.
NULL_RECORDER = NullRecorder()


class ListRecorder:
    """Collects notes in the order they happened. Handed in by the web layer."""

    def __init__(self) -> None:
        self.notes: List[Note] = []

    def note(self, stage: str, payload: dict) -> None:
        self.notes.append(
            Note(
                sequence=len(self.notes) + 1,
                stage=stage,
                side=side_of(stage),
                # Copied on the way in: the agent reuses and mutates some of
                # these dicts after noting them, and a trace that changes
                # after the fact is not a trace.
                payload=_plain(payload),
            )
        )

    def as_list(self) -> List[dict]:
        return [note.as_dict() for note in self.notes]

    def clear(self) -> None:
        self.notes = []


def _plain(value: Any) -> Any:
    """Coerce a payload to something JSON can carry, copying as it goes.

    Dates become ISO strings and tuples become lists so the trace survives
    the trip to the browser unchanged. Anything unrecognised is stringified
    rather than dropped — an unreadable note beats a missing one.
    """
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {
            name: _plain(getattr(value, name))
            for name in value.__dataclass_fields__
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
