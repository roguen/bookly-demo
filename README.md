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

Run the check suite (standard library only, no pytest) — 75 checks, and they
also run from inside the console:

```bash
python3 tests.py
```

### Golden transcripts and the narration rubric

A scenario is a file. `transcripts/*.json` holds one conversation each,
replayed by `harness.py` through the same `handle_turn` the CLI and the
console call, with the reply compared verbatim, the envelope's decision fields
compared including the literal idempotency key, and the sequence of recorder
stages compared — so a change that moved a decision to the model side of the
boundary fails a test rather than a review.

```bash
python3 harness.py
```

`rubric.py` grades the prose, because the decisions were pinned and the prose
was not. It is handed the event kind, the facts the agent gave the narrator,
and the text that came back — and nothing else, which is why grading cannot
become deciding. Findings a fixture still produces are listed in its
`known_gaps` with the issue number that will close them; an unacknowledged
finding fails, and so does an acknowledgement the rubric no longer reports —
a fix has to delete its own excuse.

A rule can also just be wrong. Asking the same question twice has one right
answer, said the same way both times, and `repeated_sentence` cannot tell
that from an agent stuck on a loop. A fixture may `accept` a finding with an
argument instead of an issue number — a separate list from `known_gaps`,
because one is a debt and the other a decision, and a defect allowed to sit
in the wrong one would never be looked at again.

To run the whole thing through a hosted narrator, which is **not** the default
path and is never what `tests.py` does:

```bash
OPENAI_API_KEY=your-key python3 harness.py --provider openai --out evidence/narration_rubric_openai.txt
```

The decision layer is compared exactly on every provider. The verbatim reply
comparison is skipped on a hosted run on purpose — a hosted model is expected
to word things differently, and that is the parity claim rather than a defect
— so the prose is graded by the rubric instead and the report says which mode
it ran in.

## The console

A local web console for the same agent — the presentation medium for a live
demo, and the way the architecture becomes legible to someone who will not
read `policy.py`.

```bash
python3 web.py
```

`http://127.0.0.1:8000`. Same constraints as everything else here: standard
library only, no `pip install`, no `npm`, no build step, no bundler, and no
network at runtime except a hosted model call you opt into. It works on a
plane.

The layout is the argument. A three-pixel line runs down the middle: language
on one side, decisions on the other. **Every element is coloured by which side
of the boundary produced it** — model output grey, deterministic output
purple, the customer's own words neither, so they are outlined and neutral.
That is one rule with no exceptions, and it is the whole design system.

| Surface | What it shows |
| --- | --- |
| Record | the customer, their orders, and the thresholds read from `policy.py` |
| Conversation | the turns, in the two voices |
| Trace | every step of one turn, tagged with the side that produced it |
| Audit | `audit.log`, newest first, with delivery status made legible |
| Queue | escalated cases, and the append-only resolution record |
| Checks | `tests.py`, streamed, plus a provider parity view |

**Customer view / operator view.** It opens in customer view, showing only
what a shopper would see. Lifetime value and CSAT are not hidden with CSS in
that mode — they are not rendered.

**Getting around.** Clicking an order writes a question about that book into
the composer rather than sending it, so you can edit it first. After every
reply the console offers what to say next, following the reason code the turn
actually produced. Those prompts live in the profile alongside the scenarios,
not in the JavaScript.

The Audit and Queue tabs start empty and stay empty until something happens
that writes to them — only a turn that *decides* something writes an audit
line, and only an escalation opens a case. Each empty tab carries the button
that produces one.

**Switching provider.** Paste the key into the field first, then pick the
provider: the key is read when you choose. Switching makes one small call to
check the key, the model name and the network before it commits, so a broken
setup surfaces at the button rather than on the next customer turn. Selecting
a hosted provider with no key leaves the stand-in running and says so.

**Replay** plays a scripted conversation into the real interface through the
real API. Nothing is pre-recorded. `DEMO.md` is the run of show.

### The back office

The other side of the boundary, as a second process on a second port.

```bash
python3 backoffice.py
```

```bash
BOOKLY_WEBHOOK_URL=http://127.0.0.1:8787/webhook python3 web.py
```

`http://127.0.0.1:8787` — a refund ledger, an agent desk, and a read-only
policy viewer. The separation is the architectural argument rather than
packaging: the agent claims to *emit* actions rather than execute them, and if
the receiver ran inside the agent's process you would have to take that on
trust. Instead, kill it mid-conversation. The refund still decides, still
writes its audit line, and records `failed_unreachable`.

It binds the same port `stub_receiver.py` does, on purpose — it is a drop-in
receiver. Run one or the other.

Every back-office surface carries a permanent stand-in chip. The ledger's
deduplication is in memory and dies with the process, the same contract the
stub has, and the screen says so rather than implying durability it does not
have.

### The agent's voice

The agent is called **Hal**, and the interface says so. The agent does not.

It has no self-introduction and no catchphrase. Both were tried and both were
struck, for the same reason: the interface already shows who is speaking, so
an agent that announces itself every few turns reads as one with no memory,
and a line prefixed to every refusal reads as a template firing rather than an
agent talking. A persona that fires on a loop is not a persona.

What remains is `agent.persona`, which reaches a hosted model through the
narration system prompt and tells it, in as many words, not to introduce
itself or prefix a speaker label — because a hosted narrator told to
"introduce yourself" does it on every single turn. A refusal now just says
plainly why:

> The Pragmatic Programmer (BK-0987) was delivered on May 2, which is outside
> the 30-day return window, so I can't issue a refund for it.

Dropping the catchphrase dropped no reason.

**Commitments are looked up, never asserted.** When a refund posts and how
fast an escalation gets picked up are published service levels in the profile,
and both travel as *facts on the event* rather than numbers in a template.
That is what lets a hosted narrator — whose prompt forbids inventing
timeframes — state the same one: it is repeating a fact it was handed rather
than producing one. Remove a service level and the reply stops rather than
inventing a timeframe, the same way retrieval fails closed.

The agent can also explain itself. "What do you mean by limit?" after a
clarify-limit handoff, and "how long until someone gets back to me?" after any
escalation, are answered from the knowledge base like any other question. It
answers to its name too — "what is your name?" — though it never volunteers it.

**A question about a refund is not a request for one.** "When will the refund
show up?" contains the word *refund*, and used to be read as asking to start
another return — so the agent answered the most natural follow-up to a refund
by offering the returns menu again. `refund_status` handles it, names the
refund it is talking about from conversation memory, and states the posting
service level. A book already refunded in this conversation is not offered as
a candidate again either: `policy.returnable_now` judges the record and the
record has not changed, so this is the agent declining to repeat work it just
did, not policy changing its mind.

**Questions about the account, not an order.** "How many books have I
ordered?" is a different question from "where is my order", and it has an
intent of its own (`order_history`) rather than being routed to the nearest
one. That matters more than it sounds: without a door, a hosted model maps
the aggregate question onto `order_status`, the read falls back to the
likeliest single order, and the customer gets a fluent answer to a question
they did not ask — with nothing escalating, because from the state machine's
view nothing went wrong. `agent_identity` exists for the same reason: the
knowledge base could only reach it by matching on "you" and "your", which
makes "how long do you keep your records?" retrieve the identity article, and
no answer is worth reopening the retrieval floor.

**A follow-up is answered as a follow-up.** Asking "when will it arrive?"
right after asking where an order is has the same answer, and the agent says
so as a continuation rather than repeating the sentence you just read. The
facts do not change; the phrasing acknowledges that you already asked. Which
rule resolved the read is recorded in the trace already, and it reaches the
narrator as a fact — wording only, and no verdict reads it.

### Re-skinning

The demo dataset — customer, orders, catalog, knowledge base, frozen clock,
scenarios, suggested prompts, the agent's voice and the service levels — lives
in `profiles/bookly.json`. `BOOKLY_PROFILE=<name>` selects another. Standing
this up for a different company is a data edit.

Thresholds and reason codes deliberately did **not** move into the profile.
Those are policy, they live in `policy.py`, and a data file must not be able to
reach them. The line between the two is the test: a profile may change what
the agent *says* and never what it *decides*.

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

**The provider does not change the decision, and that is measured rather
than asserted.** `evidence/provider_parity.txt` is the same four-scenario
script run twice — once on the stand-in, once on `gpt-5.4-mini` through the
OpenAI API. All eight replies came back worded differently; every decision
field matched, including the idempotency key (a hash of conversation,
action, and order). The amount is `$22.50` in both because `policy.py`
reads it from the order record; neither model was asked for it, and the
extraction schema has no amount field to put one in.

The Anthropic provider is implemented but has **not** been exercised — API
access is billed separately from a claude.ai subscription, so a Pro or Max
plan alone will not authorize those calls.

One honest limit: the parity run proves the *decisions* are
provider-independent, not the *prose*. On the knowledge-base miss the
hosted model dropped the offer of a human agent that the template makes
every time. No decision moved, but that is the kind of drift a graded
narration rubric would catch, and this repo does not have one yet.

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

| File                  | What it exists to prove                             |
| --------------------- | --------------------------------------------------- |
| `policy.py`           | decisions are pure functions with reason codes      |
| `llm.py`              | the model's two jobs, and nothing else              |
| `agent.py`            | the state machine, memory tiers, when it asks       |
| `tools.py`            | facts and records out — never prose                 |
| `envelope.py`         | the boundary between deciding and executing         |
| `store.py`            | records loaded from a profile, frozen clock         |
| `recorder.py`         | the interface observes a turn, never joins it       |
| `queue.py`            | a human resolves; the verdict is never edited       |
| `covers.py`           | cover art with no files, downloads, or licences     |
| `web.py`              | the console's API — it decides nothing              |
| `backoffice.py`       | receiving is a different process from deciding      |
| `app.py`              | the CLI shell                                       |
| `stub_receiver.py`    | the orchestration layer's end of the webhook        |
| `tests.py`            | the claims, executable                              |
| `harness.py`          | a scenario is a file, replayed and compared         |
| `rubric.py`           | prose is graded; grading decides nothing            |
| `transcripts/*.json`  | the golden transcripts, one per scenario            |
| `demo.txt`            | the four scripted scenarios                         |
| `profiles/bookly.json`| the dataset, so a re-skin is a data edit            |
| `docs/DECISIONS.md`   | why the repo is the way it is, with what was rejected|
| `DEMO.md`             | the run of show                                     |

## Assumptions and limits

- One demo customer is signed in (`C-1001`); authentication is out of scope.
- The customer has 38 orders, 35 of them delivered, and the clarifying
  question offers the two a return could actually be granted on. Offering a
  book and then refusing it costs the customer a turn to be told no, so
  `policy.returnable_now` filters candidates through `decide_return` rather
  than applying a rule of its own — there is deliberately no second definition
  of "returnable". An order outside the window is still perfectly reachable by
  name, and still answers with a reason code; what changed is only what the
  agent volunteers.
- The agent's name, its persona, the published service levels and the
  customer's name are profile data. They change what the agent *says* and
  nothing it decides, which is the line the profile is not allowed to cross.
- The store and clock are mocked so every run is deterministic.
- The knowledge base is deliberately small and deliberately has gaps —
  retrieval returning nothing on a gap is designed behavior, not a bug.
- Article keywords carry topic only. "Long" and "take" are how English forms
  a duration question and appear in questions about everything; with them in
  the keyword sets, two question-form words outvoted the one topical word and
  "how long do refunds take" retrieved the *shipping* article. The floor
  counts matches and cannot weigh them, so the weighing is done by curating
  what counts as a keyword.
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
- A write additionally requires that the customer *named* the book. A word
  that merely appears inside a title is a coincidence — "I want to return the
  left one" is not a reference to The Left Hand of Darkness — and a
  coincidence asks the clarifying question instead of acting. Which words are
  generic is catalog data (`catalog.generic_words`); how much of a title has
  to match is disambiguation and lives in `policy.py`. The cost is that a
  title whose every long word is generic cannot be named directly and needs
  the clarifying question: one extra turn, in the safe direction.
- The rules-based stand-in has a fixed phrase vocabulary. Turns it cannot
  classify ("pick up where we left off", keyword-free demands) fall to a
  safe help reply rather than a guess; the hosted model narrows this gap.
- Adversarial testing showed injected instructions in free text can *pose*
  extra requests (each judged by policy like any other request) but cannot
  change any verdict, amount, or reason code — see
  `injection_changes_nothing` in `tests.py` and `evidence/`.
- Repeated escalations in one conversation share one idempotency key, so a
  downstream consumer posts a single case however often the customer pushes.
- The console has no authentication, no multi-tenancy and no database, and it
  binds `127.0.0.1` only. It is a demo console for one person at one desk.
- The review queue is a JSON file that both processes read. That is correct
  for one reviewer and would not survive several; durable coordination is the
  orchestration layer's problem, not this one's.
- `queue.py` shadows the standard library's `queue` module. Everything this
  build uses is unaffected and checked on 3.9 and 3.13+;
  `concurrent.futures.ThreadPoolExecutor` is not, and nothing here uses it.
- The console's API key handling keeps the key in process memory for the
  session. That is the right shape for a demo and not a secrets manager.
