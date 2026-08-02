# Reading Guide

One pass through the code, in the order that makes the argument. Each file
exists to prove one thing; read for that thing.

## The claim

**The language model never decides — it only converses.** Every file either
enforces that boundary or demonstrates it.

## The order

**1. `policy.py` — decisions are pure functions.**
No LLM import, no free text, no I/O. Verdicts and reason codes are computed
from order records and a `today` parameter, so every decision is unit-testable
and identical on every run. All thresholds (return window, clarify limit,
dispute trigger) live here together, because they are policy, not code detail.

**2. `llm.py` — the model has exactly two jobs.**
Extraction (turn → slots) and narration (structured event → English). There is
no amount field to extract into and no verdict the narrator can change. The
regex stand-in ships as the default provider — the jobs are narrow enough that
it works, which is why the demo needs no API key. `AnthropicProvider` swaps in
a hosted model without touching any other file; its output is validated to the
same slot shapes.

**3. `agent.py` — the state machine decides when to ask, never what to grant.**
One entry point (`handle_turn`), three memory tiers with three lifetimes:
turn (a local variable), conversation (the object), customer (the store).
The intent-switching precedence rule is the file's heart: a turn that answers
the pending question is a continuation; a turn that answers nothing and names
a different intent is a topic change; asking about an order is never choosing
it. Clarification is economic — ask only when more than one order could take
the write — and bounded, two failed attempts then a human.

An intent surface is a boundary too. A question with no intent to land in
does not fail loudly — a hosted model maps it to the nearest one it has, and
the answer comes back fluent and wrong. `order_history` and `agent_identity`
exist because "how many books have I ordered" and "what is your name" were
being answered as if they were "where is my order". And `out_of_scope` is the
door for the rest: a request that fits no known intent is declined honestly and
offered a person, not force-fit onto the nearest one — and a second out-of-scope
turn in a row escalates, so uncovered questions reach a human, bounded.

**4. `tools.py` — facts and records out, never prose.**
Order lookups scoped to the signed-in customer, and retrieval with a hard
floor: fewer than two whole-word keyword matches, or a tie, returns nothing.
A miss is an honest "I don't know", never the nearest article.

**5. `envelope.py` + `reconcile.py` — deciding and executing are different systems.**
The agent emits an envelope; it never calls a refund API. The idempotency key
is `sha256(conversation|action|order_id)`, so replays and retries collapse to
one write downstream. The audit line is written before the network hop, so a
failed delivery never loses the decision — and it no longer loses the delivery
either: the envelope waits in a durable outbox, and `reconcile()` re-delivers it
with backoff (dead-lettering after its attempts), which the receiver dedups on
the key. Exactly once, across a failure.

**6. `store.py`, `app.py`, `stub_receiver.py` — mocks and shell.**
`store.py` holds the records and the frozen clock (determinism lives with the
mock data). `app.py` is presentation only. `stub_receiver.py` is the simple
in-memory drop-in for the webhook, demonstrating duplicate suppression; the real
orchestration end — durable dedup, the outbox, reconcile — is `backoffice.py`
plus `reconcile.py`, run instead of the stub.

**7. `tests.py` — the claims, executable.**
87 dependency-free checks, runnable from a terminal or from inside the
console. The ones to point at under questioning: `injection_changes_nothing`
(the thesis, tested), `web_layer_emits_identical_envelopes` (the same
scenarios through HTTP and through `Agent`, every decision field compared —
the answer to "did you just bolt a UI onto it"), `queue_resolution_is_append_only`
(a human may override an outcome and may not rewrite the record), and
`transcript_return_with_clarification` (exact strings on purpose — a golden
transcript under `transcripts/`, generated from the file rather than written
as a function, so adding a scenario is adding a file).

**7a. `harness.py`, `transcripts/*.json` — a scenario is a file.**
The harness replays a fixture through the same `handle_turn` the CLI calls and
compares the reply verbatim, the envelope's decision fields including the
literal idempotency key, and the sequence of recorder stages. That last one is
the architectural claim as a regression test: a change that moved a decision
to the model side of the boundary fails here. Which side each stage sits on is
deliberately *not* in the fixture — it is read from `recorder.STAGE_SIDES`, so
there is no second copy to drift.

**7b. `rubric.py` — prose is graded, and grading decides nothing.**
Decisions are pinned; prose is what the customer actually reads, and it was
unpinned. The rubric is handed exactly three things per narration — the event
kind, the facts the agent already gave the narrator, and the text that came
back. No `policy`, no `store`, no order record. It cannot judge whether a
refund was correct because it is never told what the refund was, and
`the_rubric_cannot_reach_a_decision` asserts that by grep, the same way the
back-office check does. Rules are mechanical: a fact the event carried must
survive, `kb_miss` must still offer a human, no number may appear that no fact
supports, and no sentence may be said twice in one conversation.

Findings a fixture still produces are listed in its `known_gaps` with the
issue that will close them. An unacknowledged finding fails, and so does an
acknowledgement the rubric no longer reports — the fix has to delete its own
excuse, which is what keeps that list from becoming where failures go to be
forgotten. A rule that is simply wrong for a case is `accept`ed instead, with
an argument rather than an issue number: `repeated-question-same-answer` is
the one that exists, because two identical questions have one identical
answer and varying it would be the worse reply.

**8. The console layer — the claim, made visible.**
Phase 2 adds no decision logic; it makes the existing boundary legible in a
browser. `recorder.py` is a null object the agent talks to, so the CLI's
behaviour cannot depend on whether anyone is watching. `web.py` serves the
same `handle_turn` the CLI calls and computes nothing. `queue.py` is where
escalations land, append-only, so an override adds an event and never edits
the verdict. `backoffice.py` runs on a second port on purpose — the agent
claims to emit rather than execute, and you can kill the receiver mid-turn to
prove it, then reconcile and watch the refund post exactly once. It also holds
the two surfaces built later: the **policy editor**, where a non-engineer
authors the CX thresholds through a validated, append-only document the console
reads live, and the **durable ledger** that dedups across a restart.
`covers.py` draws a jacket from a hash, and serves a hand-drawn override from
`covers/` when one exists. `profiles/*.json` holds the dataset, so re-skinning
is a data edit.

Read them in that order if you want the console; skip all of them if you only
want the argument, because none of them can change an outcome.

## If you want the reasoning

`docs/DECISIONS.md` is the decision record: 51 entries, each with the
reasoning, the alternative that was rejected, and where the decision is
enforced. Several record a diagnosis that turned out to be wrong and was
corrected — kept deliberately, because "we thought X, it was actually Y" is
worth more than Y on its own.

It is assembled from the sources that already carried the rationale — the
commit messages, the closed issues, the code comments, the deck's speaker
notes and the documentation — so that none of it has to be reconstructed from
memory by a later reader, or under questioning.

## Glossary

| Term | Meaning |
| --- | --- |
| **slot** | one structured field extracted from a customer turn |
| **request** | one ask within a turn (a turn can carry several) |
| **event** | the structured bundle handed to the provider to phrase |
| **candidates → options** | orders that could take the write; once presented they are the numbered options the customer picks from |
| **procedure** | a multi-turn flow that ends in a write (the return) |
| **verdict** | the policy engine's complete answer: decision, reason code, amount |
| **reason code** | the stable constant naming why the verdict is what it is |
| **envelope** | the emitted action record; the boundary between deciding and executing |
| **turn** | one customer message and the agent's reply |
| **provider** | the LLM backend: rules-based stand-in or hosted model |
| **pending question** | the clarifying question the agent is waiting on |
| **memory tiers** | turn / conversation / customer, with three lifetimes |
