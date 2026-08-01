"""Grading prose, without becoming a second place that decides anything.

The decisions in this build are pinned. `policy.py` computes them, `tests.py`
asserts them, and `transcripts/*.json` holds them field by field. The prose is
not pinned, and prose is the only part of this the customer actually reads.

That gap is not hypothetical. `evidence/provider_parity.txt` records a hosted
run where every decision field matched and the knowledge-base miss quietly
dropped the offer of a human agent that the template makes every time. No
verdict moved. Every check passed. The customer got a worse answer.

## What this module is allowed to know

Exactly three things per narration: the **event kind**, the **facts the agent
already handed the narrator**, and the **text that came back**.

No `Order`. No `Verdict`. No `policy` import, no `store` import, no `tools`
import. It cannot judge whether a refund was correct, because it is never told
what the refund was. It cannot tell you the return window, because nothing
hands it one. If a rule here ever needed a record or a threshold to do its
job, that would be the signal that grading had turned into deciding, and the
rule would be wrong rather than the boundary.

The rules are mechanical. There is deliberately no model in the grading seat:
an LLM judge would make the harness nondeterministic, network-dependent, and
would put a language model back in the position this entire repo exists to
keep it out of.

## What it produces, and where that goes

Findings. A list of them, and nothing else. A finding cannot alter a reply, a
verdict, an envelope, a reason code, or the next turn — it is computed after
the turn is over, from the recorder's notes, in the harness process. Nothing
on the decision path imports this module, and
`the_rubric_cannot_reach_a_decision` asserts that structurally, the same way
`back_office_returns_nothing_that_reaches_a_verdict` does. Findings travel
outward, exactly like a queue resolution.

## The rules

**must-carry** — a fact the event carried has to survive into the prose.
Grounded in the event's own facts, so the rubric cannot invent a requirement
it was not handed. Values are matched by their salient tokens rather than
their formatting, so a hosted model that writes "$22.50" and a template that
writes "$22.50" and a narrator that writes "22.5 dollars" all pass.

**must-offer** — an event kind whose answer carries a commitment has to keep
it. `kb_miss` must offer a human. Expressed as a class of accepted phrases
rather than an exact string, so a model that offers a human in its own words
passes and one that drops the offer fails. This is the recorded drift.

  Deliberately not applied to `escalation`, though it is tempting. That
  event's reply is legitimately reason-code-dependent: the
  ORDER_NOT_OWNED_BY_CUSTOMER branch must NOT offer a human, because the
  reply has to be indistinguishable from "no such order" or a guessed order
  id becomes an oracle. Encoding that exception would mean this module
  reading reason codes and holding an opinion about what they imply, which
  is the first step to grading becoming deciding. It is left ungraded on
  purpose, and named here so the omission is a decision rather than an
  oversight.

**must-not-invent** — the guard rail. No number in the prose that is not
traceable to a fact (this is where an injected `$500` dies, and where a
promise nobody authorised shows up). No ISO date, because a customer should
not be reading database values. No speaker label, because the interface
already says who is talking and prose that says it again is a duplicated
identity.

**transcript rules** — some defects are only visible across turns. A sentence
repeated verbatim in two different replies is never a feature: it is either a
persona line firing on a loop or an agent answering a new question with an old
answer, and from the customer's seat both read as not being listened to.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# What a finding is.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One graded defect. Inert: nothing downstream branches on it."""

    rule: str
    where: str
    detail: str

    def __str__(self) -> str:
        return "[%s] %s: %s" % (self.rule, self.where, self.detail)


# ---------------------------------------------------------------------------
# must-carry
# ---------------------------------------------------------------------------

# Which facts have to survive into the prose, per event kind. Only facts the
# event already carries appear here — this table names which of them are
# load-bearing for the customer, not what any of them mean.
#
# kb_answer is deliberately absent: its whole fact is a paragraph of article
# copy, and requiring a paraphrase to contain it would be an exact-string test
# wearing a rubric's clothes.
MUST_CARRY: Dict[str, Tuple[str, ...]] = {
    "refund_approved": ("order_id", "amount"),
    "return_denied": ("order_id",),
    "status_report": ("order_id",),
    "escalation": ("response_target",),
}

# Event kinds whose facts include a list of options, every one of which has to
# reach the customer. A clarifying question that silently drops an option is
# asking about a choice it did not offer.
MUST_LIST_OPTIONS = ("clarify_which_order", "reask_which_order")


# ---------------------------------------------------------------------------
# must-offer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhraseClass:
    """A commitment, and the words that count as keeping it."""

    what: str
    any_of: Tuple[str, ...]

    def kept_by(self, text: str) -> bool:
        lowered = text.lower()
        return any(word in lowered for word in self.any_of)


A_HUMAN = PhraseClass(
    "an offer to involve a person",
    ("human", "agent", "person", "someone", "somebody", "representative",
     "colleague", "team"),
)

MUST_OFFER: Dict[str, PhraseClass] = {
    # The recorded gap. The template offers a human every time; a hosted model
    # asked for more detail instead, and no decision moved.
    "kb_miss": A_HUMAN,
}


# ---------------------------------------------------------------------------
# must-not-invent
# ---------------------------------------------------------------------------

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

# "Hal:" or "Hal here." at the start of a reply. Name-agnostic on purpose:
# the rule is about a speaker label existing, and it stays true when the
# profile is re-skinned and the agent is called something else.
SPEAKER_LABEL_RE = re.compile(
    r"^\s*[A-Z][A-Za-z0-9'-]{1,24}\s*(?::|\s+here\s*[.,])"
)

# Numbers that are always licensed: the ordinals of a numbered list. A
# clarifying question offering two options may say "1)" and "2)" without
# either digit appearing in the facts.
MAX_LICENSED_ORDINAL = 9


# ---------------------------------------------------------------------------
# Grading one narration.
# ---------------------------------------------------------------------------


def grade_narration(kind: str, facts: dict, text: str, where: str) -> List[Finding]:
    findings: List[Finding] = []
    findings.extend(_must_carry(kind, facts, text, where))
    findings.extend(_must_offer(kind, text, where))
    findings.extend(_must_not_invent(kind, facts, text, where))
    return findings


def _must_carry(kind: str, facts: dict, text: str, where: str) -> List[Finding]:
    findings = []
    for name in MUST_CARRY.get(kind, ()):
        value = facts.get(name)
        if value is None:
            continue  # the event did not carry it, so nothing is owed
        if not _carried(value, text):
            findings.append(
                Finding(
                    "must_carry",
                    where,
                    "the event carried %s=%r and the reply does not state it: %r"
                    % (name, value, text),
                )
            )
    if kind in MUST_LIST_OPTIONS:
        for option in facts.get("options") or []:
            identifier = option.get("order_id")
            if identifier and identifier.lower() not in text.lower():
                findings.append(
                    Finding(
                        "must_carry",
                        where,
                        "option %s was offered to the customer as a choice and "
                        "does not appear in the question: %r" % (identifier, text),
                    )
                )
    return findings


def _carried(value: Any, text: str) -> bool:
    """Is this fact present, in any reasonable rendering?

    Formatting is the narrator's business. Whether the fact arrived is not.
    """
    lowered = text.lower()
    if isinstance(value, bool):
        return True  # nothing sensible to require of a flag
    if isinstance(value, (int, float)):
        return _canonical_numbers(text) >= _canonical_numbers(str(value))
    if isinstance(value, str):
        if not value.strip():
            return True
        if value.lower() in lowered:
            return True
        # A date may be stated as prose, and a long published commitment may
        # be paraphrased — in both cases the numbers in it are what has to
        # survive. "2026-07-18" is kept by "July 18"; "within 4 business
        # hours…" is kept by any sentence that still says 4.
        wanted = _canonical_numbers(value)
        return bool(wanted) and _canonical_numbers(text) >= wanted
    return True


def _must_offer(kind: str, text: str, where: str) -> List[Finding]:
    commitment = MUST_OFFER.get(kind)
    if commitment is None or commitment.kept_by(text):
        return []
    return [
        Finding(
            "must_offer",
            where,
            "%s is missing — %s always makes it, and a provider that drops it "
            "changes no decision and gives a worse answer: %r"
            % (commitment.what, kind, text),
        )
    ]


def _must_not_invent(
    kind: str, facts: dict, text: str, where: str
) -> List[Finding]:
    findings = []

    licensed = _canonical_numbers(_fact_text(facts))
    options = facts.get("options") or []
    if options:
        licensed |= {
            str(n) for n in range(1, min(len(options), MAX_LICENSED_ORDINAL) + 1)
        }
    invented = sorted(_canonical_numbers(text) - licensed)
    if invented:
        findings.append(
            Finding(
                "must_not_invent",
                where,
                "the reply states %s, which is in no fact the event carried: %r"
                % (", ".join(invented), text),
            )
        )

    iso = ISO_DATE_RE.findall(text)
    if iso:
        findings.append(
            Finding(
                "must_not_invent",
                where,
                "the reply shows %s — a customer should read a date, not a "
                "database value: %r" % (", ".join(iso), text),
            )
        )

    label = SPEAKER_LABEL_RE.match(text)
    if label:
        findings.append(
            Finding(
                "must_not_invent",
                where,
                "the reply opens with the speaker label %r; the interface "
                "already names the speaker: %r" % (label.group(0).strip(), text),
            )
        )
    return findings


def _fact_text(value: Any) -> str:
    """Every fact, flattened to one string, so numbers anywhere inside a
    nested payload count as licensed."""
    if isinstance(value, dict):
        return " ".join(_fact_text(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_fact_text(v) for v in value)
    return "" if value is None else str(value)


def _canonical_numbers(text: str) -> Set[str]:
    """Numbers in a string, normalised so formatting does not matter.

    "01" and "1" are the same number; so are "22.50" and "22.5". Comparing
    canonical forms is what lets a template say "August 1" against a fact
    that says "2026-08-01" without either being wrong.
    """
    found = set()
    for token in NUMBER_RE.findall(text):
        try:
            number = float(token)
        except ValueError:  # pragma: no cover - the pattern cannot produce it
            continue
        found.add(
            str(int(number)) if number == int(number) else str(number)
        )
    return found


# ---------------------------------------------------------------------------
# Grading a whole transcript.
# ---------------------------------------------------------------------------

# Below this, a repeated fragment is a courtesy rather than a defect.
MIN_REPEATED_SENTENCE = 15

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def grade_transcript(replies: Sequence[str]) -> List[Finding]:
    """Defects that only exist across turns."""
    findings = []
    seen: Dict[str, int] = {}
    for number, reply in enumerate(replies, start=1):
        for sentence in _sentences(reply):
            first = seen.get(sentence)
            if first is None:
                seen[sentence] = number
            else:
                findings.append(
                    Finding(
                        "repeated_sentence",
                        "turns %d and %d" % (first, number),
                        "the same sentence is said twice, so a new question "
                        "reads as an old answer: %r" % sentence,
                    )
                )
    return findings


def _sentences(reply: str) -> List[str]:
    out = []
    for raw in SENTENCE_SPLIT_RE.split(reply or ""):
        sentence = " ".join(raw.split())
        if len(sentence) >= MIN_REPEATED_SENTENCE:
            out.append(sentence)
    return out


# ---------------------------------------------------------------------------
# The whole grade for one replayed conversation.
# ---------------------------------------------------------------------------


def grade(
    turns: Iterable[Tuple[str, Sequence[Tuple[str, dict, str]]]]
) -> List[Finding]:
    """Grade a replayed conversation.

    `turns` is (reply, [(kind, facts, text), ...]) per turn — exactly what the
    recorder's narrate notes already carry, and nothing more.
    """
    turns = list(turns)
    findings: List[Finding] = []
    for number, (_reply, narrations) in enumerate(turns, start=1):
        for index, (kind, facts, text) in enumerate(narrations, start=1):
            where = "turn %d narration %d (%s)" % (number, index, kind)
            findings.extend(grade_narration(kind, facts, text, where))
    findings.extend(grade_transcript([reply for reply, _ in turns]))
    return findings
