# Bookly — a customer support agent that cannot be talked into a refund

Bookly is a support agent for a fictional online bookstore. It checks order
status, handles returns and refunds, and answers policy questions.

The whole repository rests on one claim:

> **The language model never decides. It only converses.**

The model has exactly two jobs — extraction (a customer turn becomes
structured slots) and narration (a decision that has already been made becomes
English). Eligibility, escalation, disambiguation and amounts are computed by
ordinary code in [`policy.py`](https://github.com/roguen/bookly-demo/blob/main/policy.py),
which never imports an LLM. Every file in the repo either enforces that
boundary or demonstrates it.

The reasoning: the failure that costs real money is not a clumsy sentence, it
is a confident wrong decision. So the component that can be confidently wrong
is confined to where being wrong is only cosmetic.

---

## Run it

No dependencies. No API key. Python 3.9 or later.

```bash
python3 app.py --script demo.txt   # four scripted scenarios, in a terminal
python3 tests.py                   # 82 checks, no pytest
python3 harness.py                 # the golden transcripts, and the rubric
python3 web.py                     # the console, 127.0.0.1:8000
python3 backoffice.py              # the executing side, 127.0.0.1:8787
```

Nothing above installs anything, reaches the network, or needs a build step.
A hosted model is opt-in, and the demo is designed to work on a plane.

---

## Phases

### Phase 1 — the agent · `v1.0.0`

The deterministic core. A state machine over one conversation, three memory
tiers with three lifetimes, retrieval with a hard floor that fails closed, and
action *envelopes* rather than API calls — the agent emits a refund, it never
executes one. The idempotency key is `sha256(conversation|action|order)`, so
replays and retries collapse to one write downstream.

Also phase 1: a five-slide architecture deck, and evidence captured from real
runs rather than asserted — including a hosted-model parity run where all
eight replies came back worded differently and every decision field matched.

### Phase 2 — the console · `v2.0.0` · [PR #12](https://github.com/roguen/bookly-demo/pull/12)

Phase 2 adds **no decision logic**. It makes the phase-1 claim legible in a
browser to someone who will never read `policy.py`.

- **A three-column console** whose layout *is* the boundary from slide 1 — a
  3px full-height rule with language on one side and decisions on the other.
- **Colour as provenance**, one rule with no exceptions: model output grey,
  deterministic output purple, the customer's own words neither, so they are
  outlined and neutral. That rule teaches the thesis without a sentence of
  explanation.
- **A trace of every turn**, tagged by which side of the boundary produced it,
  where any decision expands to the named policy constant behind it.
- **An append-only human review queue.** A reviewer may override an outcome
  and may not rewrite the record — the original verdict stays exactly as
  policy computed it, with the override as a separate later event naming who,
  when and why.
- **A back office on a second port.** The separation is the architectural
  argument, not packaging: kill it mid-conversation and the refund still
  decides, still audits, and records `failed_unreachable`.
- **The check suite runnable from inside the app**, plus a provider parity
  view.
- **The demo dataset in `profiles/`**, so re-skinning for another company is a
  data edit measured in minutes.

Verified for the phase-2 release: the CLI byte-identical to `v1.0.0`; a clean
clone makes zero external requests; `stub_receiver.py` untouched and its
evidence procedure reproduced key for key. Since phase 3 the check count is
enforced rather than repeated — 82 checks green on 3.9, 3.13 and 3.14, in CI
on every push, and any document that cites a different number fails the
suite.

---

### Phase 3 — the eval harness

Phase 3 adds **no decision logic either**. It pins the part of the build that
was never pinned: the prose.

- **A scenario is a file.** `transcripts/*.json`, replayed by `harness.py`
  through the same `handle_turn` the CLI and the console call. Each fixture
  pins the reply verbatim, every envelope decision field including the literal
  idempotency key, and the sequence of trace stages — so a change that moved a
  decision to the model side of the boundary fails a test rather than a review.
- **A graded narration rubric.** `rubric.py` is handed the event kind, the
  facts the agent gave the narrator, and the text that came back. Nothing else
  — no `policy`, no `store`, no order record — which is why grading cannot
  become deciding, and a check asserts that structurally in both directions.
- **A regression run**, on 3.9, 3.13 and 3.14, with no `pip install` anywhere
  in the workflow file, plus a job that boots a clean clone and reads a record
  out of the console.
- **A hosted mode that is not the default path.** `--provider openai` compares
  every decision field exactly and grades the prose instead of comparing it
  verbatim, because a hosted model wording things differently *is* the parity
  claim rather than a defect.

The rubric found four defects on its first run, offline, on the stand-in —
every one of them in prose, and not one of them able to move a verdict, which
is precisely why no existing check saw them. The measured knowledge-base drift
it was built for is now a check that runs against the recorded hosted reply
with no billed call. Open defects are listed in the fixture that produces
them with the issue that will close them, and a stale acknowledgement fails
the suite, so a fix has to delete its own excuse.

---

## Known future phases

These are in priority order, and they are the same ones the deck argues on its
final slide. Deliberately, **the console is not among them** — that slide
argues correctness and durability, and a presentation-layer item would dilute
it. The GUI is the medium, not the roadmap.

### 1. Embeddings for policy retrieval — behind the same hard floor

Keyword matching is the weakest part of the build. The *floor* is the part
that matters, and it does not move: below two whole-word matches, or on a tie,
retrieval returns nothing rather than the nearest article.

Swapping the matcher is a contained change. Removing the floor would
reintroduce the confident-wrong-article failure at higher fidelity, which is
strictly worse than the current honest miss.

### 2. Questions the intent surface does not model — closed in v3.3.0

"How many books have I ordered?" had no intent to land in, so a hosted model
mapped it to the nearest one — `order_status` — and answered about a single
order, confidently. `order_history` and `agent_identity` gave two questions
doors, but that was two instances, not the class: the agent still could not
*tell* it was out of scope, and adding intents one at a time does not fix that.

v3.3.0 closes the class. `out_of_scope` is a door for "none of the above": the
model classifies a request that fits no known intent as out of scope instead of
force-fitting it onto the nearest one, and the agent names the limit and offers
a person rather than answering the nearest thing it can. A second out-of-scope
turn in a row escalates — the dispute pattern applied to scope — so the failure
mode this build has always advertised, that anything uncovered reaches a human,
finally fires, and stays bounded so a stray question is not a case. The door
provably swallows nothing answerable: a check asserts every handled question
still routes to its own intent, never to `out_of_scope`.

### 3. The orchestration layer becoming real

Retries, dead-letter handling, and a durable idempotency store on the
receiving end — none of which this repo implements, and all of which the
envelope contract already accommodates. That is *why* the agent emits instead
of executing.

The ledger in `backoffice.py` deduplicates in memory and dies with the
process, exactly like `stub_receiver.py`, and both screens say so rather than
implying a durability they do not have. Durable dedup is the real receiver's
job.

### Deliberately out of scope

- **No authorable *rules* — yet.** As of v3.2.0 the three CX thresholds are
  authorable for real: a non-engineer edits them from the back office, each
  change validated against its bounds, attributed, and appended to a log the
  console reads live, while the decision structure and the two floors that stop
  a confidently wrong answer stay in `policy.py`. What is still out of scope is
  authoring new *rules* — a procedure DSL, not just tuning numbers — which is
  the harder next step and is not smuggled into a parameter-editing branch.
- **No supervisor agent or tool-calling loop.** Orchestration here is a state
  machine on purpose. A supervisor is one more component that can be
  confidently wrong, placed exactly where being wrong is expensive.
- **No auth, multi-tenancy or database.** The console binds `127.0.0.1` and is
  a demo console for one person at one desk.

---

## Where things live

| File | What it exists to prove |
| --- | --- |
| `policy.py` + `policy.json` | decisions are pure functions with reason codes; the CX thresholds are authorable through a validated, append-only document |
| `llm.py` | the model's two jobs, and nothing else |
| `agent.py` | the state machine, memory tiers, when it asks |
| `tools.py` | facts and records out — never prose |
| `envelope.py` | the boundary between deciding and executing |
| `recorder.py` | the interface observes a turn, never joins it |
| `queue.py` | a human resolves; the verdict is never edited |
| `web.py` | the console's API — it decides nothing |
| `backoffice.py` | receiving is a different process from deciding |
| `covers.py` + `covers/` | drawn cover art — hand-drawn per book, a generated fallback, no downloads or licences |
| `store.py` + `profiles/` | records as data, so a re-skin is a data edit |
| `tests.py` | the claims, executable |

**Start here:** `READING_GUIDE.md` walks the repo in the order that makes the
argument. `DEMO.md` is a twelve-minute run of show written for a VP of
Customer Experience and a VP of Engineering at the same time. `evidence/`
holds transcripts and audit trails captured from real runs.
