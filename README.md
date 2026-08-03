# Bookly Support Agent

A customer support agent for Bookly, a fictional online bookstore. It checks
order status, handles returns and refunds, and answers policy questions.

One claim holds the build together: **the language model never decides — it
only converses.** The model has exactly two jobs, extraction (customer turn →
structured slots) and narration (structured decision → English). Eligibility,
escalation, disambiguation and amounts are computed in `policy.py`, which never
imports an LLM.

## Three ways to run it

No dependencies, no API key, no build step. Python 3.9 or later. All three run
the **same agent** through the same `handle_turn` — the interface changes, the
decisions do not.

### 1. The console — start here

```bash
python3 web.py     # http://127.0.0.1:8000
```

A local web console, and the best way to see the argument. It opens in
**customer view**, looking like an ordinary support chat; one click switches to
**operator view**, which splits the screen down the middle — language on one
side, decisions on the other — and shows the trace, the audit log, the
escalation queue, and the check suite streaming live. Vanilla JS, one
stylesheet, no bundler.

### 2. The console with the back office

```bash
python3 backoffice.py                                            # terminal 1
BOOKLY_WEBHOOK_URL=http://127.0.0.1:8787/webhook python3 web.py  # terminal 2
```

Adds the executing side on `127.0.0.1:8787` — a refund ledger, an agent desk,
and a policy editor. Two processes, because the agent claims to *emit* actions
rather than execute them, and that is only demonstrable if the receiver can be
killed independently. The console runs fine without it; envelopes simply record
`failed_unreachable` instead of being delivered.

### 3. The CLI

```bash
python3 app.py                     # a live session
python3 app.py --script demo.txt   # four scripted scenarios
```

No browser, same decisions. The scripted run walks an order lookup with a
follow-up answered from memory; a return where the agent asks which book before
acting; two policy questions, one answered and one where retrieval fails closed
rather than serving the nearest article; and an out-of-window denial where
pressure to override escalates to a human instead of flipping the verdict.

## Verifying the claim

The point of the claim is that it is checkable, not that it is stated well.

```bash
python3 tests.py     # 90 checks, standard library only, no pytest
python3 harness.py   # 9 golden transcripts, replayed and compared
```

`tests.py` asserts the boundary structurally — that `policy.py` imports no
model, that no extracted slot reaches a verdict, that injected instructions
change no decision. `harness.py` replays whole conversations from
`transcripts/*.json` through the same `handle_turn` the CLI and console call,
comparing the reply verbatim and every envelope field including the idempotency
key, so a decision that drifted to the model side fails a test rather than a
review.

`rubric.py` grades the prose, because the decisions were pinned and the prose
was not. It sees only the event kind, the facts the agent handed the narrator,
and the text that came back — never the order record — which is why grading
cannot become deciding.

CI runs all of it on Python 3.9, 3.13 and 3.14, plus a clean-clone boot.

## Inside the console

The layout is the argument. In operator view a line runs down the middle:
language on one side, decisions on the other. **Every element is coloured by
which side produced it** — model output grey, deterministic output purple, the
customer's own words neither. One rule, no exceptions.

| Surface | What it shows |
| --- | --- |
| Record | the customer, their orders, thresholds read live from `policy.py` |
| Conversation | the turns, in the two voices |
| Trace | every step of one turn, tagged with the side that produced it |
| Audit | `audit.log`, newest first, with delivery status |
| Queue | escalated cases and the append-only resolution record |
| Checks | `tests.py`, streamed, plus a provider parity view |

Customer view is not the same screen with fields hidden. Lifetime value, CSAT
and reason codes are **not rendered** there at all — a claim about the document,
not about the paint.

Clicking an order writes a question about that book into the composer rather
than sending it, so you can edit first. After each reply the console offers what
to say next, following the reason code the turn actually produced — and those
prompts live in the profile, not the JavaScript. **Replay** plays a scripted
conversation through the real API; nothing is pre-recorded.

## Action envelopes

The agent never calls a refund API. It emits an envelope keyed by
`sha256(conversation|action|order_id)`; the orchestration layer owns delivery
and the write. The audit line is written *before* the network hop, so a failed
delivery never loses the record of the decision. A failed hop waits in a
durable outbox, `reconcile` re-delivers it, and the receiver's durable ledger
suppresses the duplicate on its key — delivery is at-least-once, and that
dedup is what makes the *posting* exactly once.

## Using a hosted model

By default both model jobs run on a rules-based stand-in, so the demo is
deterministic and dependency-free.

```bash
pip install openai && OPENAI_API_KEY=... python3 app.py
pip install anthropic && ANTHROPIC_API_KEY=... python3 app.py
```

**The provider does not change the decision, and that is measured rather than
asserted.** `evidence/provider_parity.txt` is the same script run on the
stand-in and on `gpt-5.4-mini`: all eight replies came back worded
differently, every decision field matched, including the idempotency key. The
amount is `$22.50` in both because `policy.py` reads it from the order record —
the extraction schema has no amount field to put one in.

`BOOKLY_PROVIDER` forces a choice; two vendor keys without it is refused rather
than resolved by precedence. The Anthropic path is implemented but unexercised —
API access is billed separately from a claude.ai subscription.

## Files

| File | What it exists to prove |
| --- | --- |
| `policy.py` | decisions are pure functions with reason codes |
| `llm.py` | the model's two jobs, and nothing else |
| `agent.py` | the state machine, memory tiers, when it asks |
| `tools.py` | facts and records out — never prose |
| `envelope.py` | the boundary between deciding and executing |
| `store.py` + `profiles/` | records as data, so a re-skin is a data edit |
| `recorder.py` | the interface observes a turn, never joins it |
| `queue.py` | a human resolves; the verdict is never edited |
| `web.py` | the console's API — it decides nothing |
| `backoffice.py` | receiving is a different process; durable ledger, policy editor |
| `reconcile.py` | re-delivers a failed hop, dead-letters after its attempts |
| `covers.py` + `covers/` | drawn cover art — no downloads, no licences |
| `tests.py` | the claims, executable |
| `harness.py` + `transcripts/` | a scenario is a file, replayed and compared |
| `rubric.py` | prose is graded; grading decides nothing |

## Assumptions and limits

- One demo customer is signed in (`C-1001`); authentication is out of scope.
- The store and clock are mocked, so every run is deterministic.
- The knowledge base is small and has deliberate gaps. Retrieval returning
  nothing on a gap is designed behaviour, not a bug.
- A write requires the customer to have *named* the book. A word that merely
  appears inside a title is a coincidence, and a coincidence asks the
  clarifying question instead of acting.
- Cases the policy engine does not model escalate rather than resolve. That is
  the intended failure mode.
- Injected instructions in free text can *pose* extra requests — each judged by
  policy like any other — but change no verdict, amount or reason code. See
  `injection_changes_nothing`.
- The rules-based stand-in has a fixed phrase vocabulary; turns it cannot
  classify fall to a safe help reply rather than a guess.
- The console has no authentication, multi-tenancy or database, and binds
  `127.0.0.1` only. It is a demo console for one person at one desk.
- The review queue is a JSON file both processes read — correct for one
  reviewer, and not for several.
- `queue.py` shadows the standard library's `queue`. Nothing this build uses is
  affected; `concurrent.futures.ThreadPoolExecutor` would be, and nothing here
  uses it.
- API keys live in process memory only — never on disk, in logs, in URLs, or in
  `os.environ`, which every subprocess would inherit.

## Where to look next

- **`READING_GUIDE.md`** — the repo in the order that makes the argument,
  starting at `policy.py`.
- **`docs/DECISIONS.md`** — 57 entries: the decision, the alternative rejected,
  where it is enforced, and the sceptic's question it answers. Several record a
  diagnosis that was wrong and later corrected; those are the useful ones.
- **`evidence/`** — transcripts and audit trails captured from real runs.
