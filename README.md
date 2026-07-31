# Bookly Support Agent

A customer support agent for Bookly, a fictional online bookstore. It checks
order status, handles returns and refunds, and answers policy questions.

The architecture rests on one claim: **the language model never decides — it
only converses.** The model has exactly two jobs: extraction (customer turn →
structured slots) and narration (structured decision → English). Eligibility,
escalation, and disambiguation are computed by deterministic code in
`policy.py`, which never imports an LLM.

## Quickstart

No dependencies, no API key. Python 3.9 or later.

```bash
python3 app.py --script demo.txt
```

walks four scenarios:

1. **Order status** — a lookup, then a follow-up ("When will it arrive?")
   resolved from conversation memory.
2. **A return** — the agent asks a clarifying question before acting, then
   emits a refund envelope.
3. **Policy questions** — one the knowledge base answers, and one where
   retrieval fails closed rather than serving the nearest article.
4. **An out-of-window return** — denied with a reason code; pressure to
   override escalates to a human instead of flipping the verdict.

For a live session:

```bash
python3 app.py
```

Run the tests (standard library only, no pytest):

```bash
python3 tests.py
```

## How a turn flows

```
customer turn
     │
     ▼
extract (llm.py) ──► slots: intents, order refs, options
     │
     ▼
orchestrate (agent.py) ── state machine + three memory tiers
     │
     ├─► tools.py ──► facts: order records, policy articles (or nothing)
     │
     ├─► policy.py ──► verdict + reason code  (no LLM here, ever)
     │
     ├─► envelope.py ──► action envelope: emitted, audited, delivered
     │
     ▼
narrate (llm.py) ──► the reply the customer reads
```

Memory is three tiers with three lifetimes:

| Tier         | Lives in            | Lifetime           |
| ------------ | ------------------- | ------------------ |
| Turn         | `handle_turn` local | one turn           |
| Conversation | the `Agent` object  | one conversation   |
| Customer     | `store.py` records  | durable            |

## Action envelopes

The agent never calls a refund API. It emits an envelope with an idempotency
key derived from `sha256(conversation|action|order_id)`; the orchestration
layer owns delivery and the actual write. To watch envelopes arrive, run the
stub receiver in a second terminal:

```bash
python3 stub_receiver.py
```

then run the demo with the webhook set:

```bash
BOOKLY_WEBHOOK_URL=http://127.0.0.1:8787/webhook python3 app.py --script demo.txt
```

Each envelope shows `delivery=delivered_200`. Send the same envelope twice
and the receiver flags the duplicate — that is the idempotency contract.
The audit line is written to `audit.log` before the network hop, so a failed
delivery never loses the record of the decision.

## Using a hosted model

By default the two model jobs run on a rules-based stand-in so the demo is
dependency-free and deterministic. Because both jobs are narrow and
structured, the stand-in is serviceable.

**Everything in `evidence/` was produced by the stand-in.** The Anthropic
provider is implemented but has not been exercised — treat it as untested
code. Note also that API access is billed separately from a claude.ai
subscription, so a Pro or Max plan alone will not authorize these calls.

What does *not* depend on the provider: the extraction schema has no amount
field, and `policy.py` re-derives every verdict, reason code, and amount
from the order record, so no model — regex or hosted — can alter a decision.
What a hosted model would change is slot-extraction quality on messier
phrasing. Verifying that is the first job of the eval harness.

To use a hosted model instead:

```bash
pip install anthropic
ANTHROPIC_API_KEY=your-key python3 app.py
```

```bash
pip install openai
OPENAI_API_KEY=your-key python3 app.py
```

`BOOKLY_PROVIDER=rules|anthropic|openai` forces a choice; setting two vendor
keys without it is refused rather than resolved by precedence.
`BOOKLY_ANTHROPIC_MODEL` / `BOOKLY_OPENAI_MODEL` override the model, so a
renamed model is an env fix rather than a code change.

The OpenAI default is a **mini-tier** model on purpose. Extraction and
narration are narrow and structured, which is the same property that makes
the regex stand-in serviceable — a model that had to reason about
eligibility would need a frontier tier; one that only reads and phrases
does not.

Nothing else changes — the decision layer is identical either way. Both
hosted providers subclass `HostedProvider` and define exactly two things:
a name and the network call. Prompt construction, output parsing, and the
untrusted-output validation are inherited, so there is no per-vendor copy
to drift and no place for a vendor to introduce a decision.

## Files

| File               | What it exists to prove                                |
| ------------------ | ------------------------------------------------------ |
| `policy.py`        | decisions are pure functions with reason codes         |
| `llm.py`           | the model's two jobs, and nothing else                 |
| `agent.py`         | the state machine, memory tiers, when it asks          |
| `tools.py`         | facts and records out — never prose                    |
| `envelope.py`      | the boundary between deciding and executing            |
| `store.py`         | mock orders and knowledge base, frozen clock           |
| `app.py`           | the CLI shell                                          |
| `stub_receiver.py` | the orchestration layer's end of the webhook           |
| `tests.py`         | the eval harness                                       |
| `demo.txt`         | the four scripted scenarios                            |

## Assumptions and limits

- One demo customer is signed in (`C-1001`); authentication is out of scope.
- The store and clock are mocked so every run is deterministic.
- The knowledge base is deliberately small and deliberately has gaps —
  retrieval returning nothing on a gap is designed behavior, not a bug.
- Cases the policy engine does not model escalate to a human rather than
  resolve. That is the intended failure mode.
- The `[envelope …]` lines in the CLI are back-office telemetry, shown for
  demo visibility. In a deployment they go to the webhook and audit log; the
  customer sees only the `bookly>` text.
- Extraction only knows the signed-in customer's own titles — which is why
  nothing about other customers can leak, and also why a question about an
  unknown title ("my Snow Crash order") reads as title-less and falls back
  to the likeliest order. Replies always name the order they describe, so a
  wrong read is visible and cheap; writes never fall back.
- The rules-based stand-in has a fixed phrase vocabulary. Turns it cannot
  classify ("pick up where we left off", keyword-free demands) fall to a
  safe help reply rather than a guess; the hosted model narrows this gap.
- Adversarial testing showed injected instructions in free text can *pose*
  extra requests (each judged by policy like any other request) but cannot
  change any verdict, amount, or reason code — see
  `injection_changes_nothing` in `tests.py` and `evidence/`.
- Repeated escalations in one conversation share one idempotency key, so a
  downstream consumer posts a single case however often the customer pushes.
