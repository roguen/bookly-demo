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

**4. `tools.py` — facts and records out, never prose.**
Order lookups scoped to the signed-in customer, and retrieval with a hard
floor: fewer than two whole-word keyword matches, or a tie, returns nothing.
A miss is an honest "I don't know", never the nearest article.

**5. `envelope.py` — deciding and executing are different systems.**
The agent emits an envelope; it never calls a refund API. The idempotency key
is `sha256(conversation|action|order_id)`, so replays and retries collapse to
one write downstream. The audit line is written before the network hop — a
failed delivery can lose the delivery, never the decision.

**6. `store.py`, `app.py`, `stub_receiver.py` — mocks and shell.**
`store.py` holds the records and the frozen clock (determinism lives with the
mock data). `app.py` is presentation only. `stub_receiver.py` is the
orchestration layer's end of the webhook, demonstrating duplicate suppression.

**7. `tests.py` — the claims, executable.**
51 dependency-free checks, runnable from a terminal or from inside the
console. The ones to point at under questioning: `injection_changes_nothing`
(the thesis, tested), `web_layer_emits_identical_envelopes` (the same
scenarios through HTTP and through `Agent`, every decision field compared —
the answer to "did you just bolt a UI onto it"), `queue_resolution_is_append_only`
(a human may override an outcome and may not rewrite the record), and
`golden_transcript_return_flow` (exact strings on purpose — the seed of the
golden-transcript harness proposed as future work).

**8. The console layer — the claim, made visible.**
Phase 2 adds no decision logic; it makes the existing boundary legible in a
browser. `recorder.py` is a null object the agent talks to, so the CLI's
behaviour cannot depend on whether anyone is watching. `web.py` serves the
same `handle_turn` the CLI calls and computes nothing. `queue.py` is where
escalations land, append-only, so an override adds an event and never edits
the verdict. `backoffice.py` runs on a second port on purpose — the agent
claims to emit rather than execute, and you can kill the receiver mid-turn to
prove it. `covers.py` draws jackets from a hash. `profiles/*.json` holds the
dataset, so re-skinning is a data edit.

Read them in that order if you want the console; skip all of them if you only
want the argument, because none of them can change an outcome.

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
