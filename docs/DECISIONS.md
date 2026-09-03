# Decision record

Why this repo is the way it is.

Every entry is a decision where a competent engineer could reasonably have
chosen otherwise, together with the reasoning, the alternative that was
rejected, and where the decision is enforced. Several entries record a
diagnosis that turned out to be **wrong** and was corrected — those are kept
deliberately, because "we thought X, it was actually Y" is worth more than Y
on its own.

This file is generated from the sources that already carried the rationale:
the commit history, the closed issues, the code comments, the deck's speaker
notes, and the documentation. It exists so that none of that has to be
reconstructed from memory — in a later phase, by a new reader, or under
questioning in a presentation.

Ordered by how load-bearing the decision is. The claim the whole repo rests
on is first.

---

## 1. The model never decides — it only converses

*Phase 1*

**Decision.** The LLM is given exactly two jobs: extraction (customer turn → structured
slots) and narration (structured event → English). Eligibility, escalation,
disambiguation and amounts are computed by ordinary code in policy.py, which
the model layer never imports.

**Why.** The failure that costs real money is not a clumsy sentence — it is a confident
wrong decision. So the one component that can be confidently wrong is confined
to where being wrong is merely cosmetic. This makes the correctness guarantee
structural rather than empirical: no provider, prompt, or injected string can
move a verdict, because the code path that produces verdicts never consults the
model. It is also falsifiable in one line — if the language model disappeared
entirely, every function in policy.py would return the same answers — and every
later commit in the repo is measured against it. The narrowness is what makes
the rest possible: the regex stand-in is serviceable and a mini-tier hosted
model sufficient precisely because neither job requires reasoning about
eligibility.

**Rejected.** An agent that reasons about eligibility in the prompt — the conventional shape,
which puts a refund decision inside sampled text. Also rejected: a supervisor
agent or free-running tool-calling loop, which is one more component that can
be confidently wrong placed exactly where being wrong is expensive;
orchestration is a state machine on purpose, and a state machine can answer
'what is it waiting on right now' where a loop cannot.

**Answers the question.** What actually stops the model from approving a refund it shouldn't? / Isn't
this just a chatbot with extra steps?

**Lives in.** `policy.py, llm.py, agent.py, README.md, READING_GUIDE.md, docs/wiki/Home.md, deck/build.js slide 1`

---

## 2. policy.py is pure, imports no LLM, and holds every threshold as a named constant

*Phase 1*

**Decision.** Every eligibility, escalation and disambiguation rule is a pure function over
order records taking `today` as a parameter. RETURN_WINDOW_DAYS,
MAX_CLARIFY_ATTEMPTS, DENIALS_BEFORE_ESCALATION and MIN_TITLE_WORDS_FOR_WRITE
are declared together, each with its reasoning attached. The window bound is
inclusive by strict comparison (`> 30`, not `>=`).

**Why.** Purity is the enforcement mechanism, not a style preference: 'policy.py does
not import an LLM' is a structural property you can grep for, not a promise
about discipline, and discipline decays. Naming the thresholds in one place
makes each a reviewable line of policy that a verdict can be traced back to;
scattering them at their use sites would make policy an emergent property of
code. Taking `today` as an argument rather than calling date.today() is what
keeps the functions testable and every run identical. The off-by-one is
customer-visible policy, so the comment states which side day 30 falls on
rather than leaving it to the reader.

**Rejected.** Inlining the numbers at their comparison sites; reading the system clock inside
policy.

**Answers the question.** Where is the return window actually defined? Is day 30 in or out?

**Lives in.** `policy.py, store.py (TODAY), tests.py::back_office_returns_nothing_that_reaches_a_verdict, tests.py::a_verdict_traces_back_to_its_named_constant`

---

## 3. The extraction schema has no amount field, and the Verdict is closed

*Phase 1*

**Decision.** The slot schema hosted-model output is validated against structurally cannot
carry a monetary amount. Verdict is a frozen dataclass carrying decision,
reason code, order id and amount; the amount is copied from the order record
inside decide_return and nowhere else. Nothing downstream may add to it.

**Why.** This is the mechanism behind the whole injection claim. A hostile extraction
response carrying an invented intent, an injected order id and a '$500'
validates to nothing useful, because there is nowhere for the money to land —
the defence is structural absence rather than a validation rule or filter that
could be bypassed. It also means the claim can be stated precisely rather than
maximally: injection cannot change a verdict, an amount or a reason code,
because both are re-derived from the record.

**Rejected.** Letting the extractor report the refund amount it read from the record; an
input filter or injection classifier in front of the model — filtering is a
losing arms race, and naming that is why the absence of a filter is a position
rather than an omission.

**Answers the question.** What stops a customer typing 'refund me $500' and getting $500? Did you filter
for injection?

**Lives in.** `llm.py (Request, _clean_request), policy.py (Verdict, decide_return), evidence/injection_transcript.txt`

---

## 4. The agent emits action envelopes; it never executes them

*Phase 1*

**Decision.** On approval the agent writes an envelope carrying a deterministic idempotency
key. A separate receiver executes it. envelope.py contains no refund API call,
and `emit` returns a delivery string that nothing in the agent ever branches
on.

**Why.** Deciding and executing are different systems. Because the agent emits and does
not execute, the record it judges never changes underneath it, and the contract
already accommodates the retries, dead-lettering and durable idempotency store
this repo deliberately does not implement. Not branching on the delivery result
is the other half: if the agent reacted to delivery, the receiver would be back
inside the decision loop and 'emit, don't execute' would stop being true. This
property is leaned on repeatedly later — replayed demos, re-pressed denials, a
re-offered refunded book — and it is also why the agent has to remember its own
issued refunds separately from what policy would allow.

**Rejected.** Calling the refund API inline from the turn handler — named on the deck slide
as the simplicity that was given up. Also rejected: implementing retries inside
the agent to make the demo look complete.

**Answers the question.** Where does the money actually move? What happens if the customer presses the
button twice?

**Lives in.** `envelope.py, agent.py (_emit_refund, _note_envelope)`

---

## 5. The idempotency key is derived from the decision, not generated

*Phase 1*

**Decision.** `sha256(conversation_id | action | order_id)`. The same decision emitted,
retried or replayed hashes to the same value. `envelope_id` is a fresh uuid4
every time and is deliberately excluded from what fixtures pin. An escalation
can precede order resolution, so 'unresolved' is the stable third component
rather than an empty string.

**Why.** A UUID would differ on every emission, which is exactly what you do not want:
identity here is identity of the *decision*, not of the message. It is what
lets a downstream receiver post exactly once across replays and retries with
the agent knowing nothing about delivery — and it is the instrument that makes
the parity result meaningful, since two providers phrasing a refund completely
differently produce the identical key. Replaying the hero sequence twice
therefore lands downstream as suppressed duplicates, demonstrating the contract
by repeating yourself.

**Rejected.** A random per-emission id as the dedup key; keying on the conversation text.

**Answers the question.** Isn't the idempotency key just a UUID with extra steps?

**Lives in.** `envelope.py (idempotency_key), evidence/duplicate_receipt.txt, transcripts/hero-sequence.json`

---

## 6. The audit line is written before the network hop

*Phase 1*

**Decision.** `_audit` records the emitted envelope, then delivery is attempted, then the
delivery result is audited as a second line. An unreachable receiver normalises
to the single string `failed_unreachable`, with the platform errno text
stripped; an HTTP error keeps its status because the receiver answered.

**Why.** A failed delivery can lose the delivery. It can never lose the decision. That
is the difference between a system you can reconcile and one you cannot, and it
is the fact the whole failure demo rests on: kill the receiver mid-conversation
and the refund still decides at $22.50 and still writes its audit line.
Normalising the error string matters because it is read off a screen — '[Errno
61] Connection refused' is platform noise where 'failed_unreachable' is the
point.

**Rejected.** Auditing only on successful delivery.

**Answers the question.** What happens if the downstream system is down?

**Lives in.** `envelope.py (emit, _deliver), DEMO.md §7, tests.py::decision_survives_an_unreachable_receiver`

---

## 7. Ambiguity on a write path always asks — and asking is bounded

*Phase 1*

**Decision.** The agent asks only when more than one order could take the write, and asks at
most twice (MAX_CLARIFY_ATTEMPTS = 2) before handing to a human; an unparseable
answer consumes one of the attempts. Option parsing is deliberately tight: a
bare digit must be the whole reply, cardinals only in replies of three words or
fewer, and any deferral phrase ('just pick one', 'either one', 'whatever') is
screened first and yields no choice at all.

**Why.** Asking a clarifying question costs one turn; guessing on a write path costs a
wrong refund — so we pay the turn, but not forever, because an unbounded loop
traps the customer instead of getting them a person. The parsing rules are each
bounded by how much confidence the form actually carries: a stray '2' inside a
sentence is a quantity, not a choice, and a number word inside a delegating
reply is the customer handing the choice back, not making it. Counting invalid
answers toward the limit is what stops a customer who cannot express the choice
looping forever against an agent that keeps asking the same question.

**Rejected.** Letting the model pick the most likely order when candidates are ambiguous;
matching any digit anywhere in the reply; asking indefinitely.

**Answers the question.** Doesn't it ask more questions than a human agent would? What does it do when
the customer says 'just pick one'?

**Lives in.** `policy.py (MAX_CLARIFY_ATTEMPTS, clarify_limit_reached, should_clarify), agent.py (_reask_or_escalate), llm.py (OPTION_DIGIT_RE, ORDINAL_RE, CARDINAL_RE, DEFERRAL_RE)`

---

## 8. Repeated pressure escalates; it never flips the verdict

*Phase 1*

**Decision.** DENIALS_BEFORE_ESCALATION = 1. A second attempt at an already-denied request
becomes an escalation layered over the denial, and the reason code underneath
stays exactly what policy computed. Escalation can only turn a denial into
human review — never into an approval.

**Why.** A customer pressing a request policy denied is not new information; it is a
dispute, and disputes belong with someone who has authority. Keeping the
machine's answer intact means the record shows what policy computed rather than
what pressure produced. Stating the ceiling out loud — escalation can never
become approval — pre-empts the obvious follow-up that pressure eventually
wins.

**Rejected.** Softening or reversing the verdict under repetition.

**Answers the question.** Can a customer argue the agent into a refund?

**Lives in.** `policy.py (escalate_if_disputed), transcripts/denial-then-escalation.json, transcripts/hero-sequence.json turn 5, evidence/demo_transcript.txt scenario 4`

---

## 9. Retrieval fails closed, and article keywords carry topic only

*Phase 1*

**Decision.** MIN_KEYWORD_MATCHES = 2 distinct whole-word hits; below the floor, or on a tie
for best, `search_policy` returns None rather than the nearest article.
Matching is set intersection on tokens from a single `_tokenize`, not substring
containment. Question-form words ('long', 'take') were later stripped from
article keyword sets.

**Why.** A wrong-but-plausible article that reaches the customer is worse than an honest
miss — nothing is invented, something adjacent is retrieved and presented as
responsive, which is the failure that actually reaches customers. The knowledge
base is deliberately small with deliberate gaps, because the gaps are the test.
Two corrections are recorded here. First: the floor was raised to two after
'customs for Ireland' retrieved the domestic shipping article via 'ship'
matching inside 'shipping' — the deck tells that story, and that fix was right
but incomplete, because it addressed too *few* matches rather than *generic*
ones. Second: 'how long do refunds take' scored 2 on the shipping article
('long' + 'take') and beat the return-policy article 2-1. Those two words are
not about shipping; they are how English forms a duration question. The floor
counts matches and cannot weigh them, so the weighing is done by curating what
counts as a keyword — the same lesson as the title-word guard, one subsystem
over.

**Rejected.** Returning the best-scoring article regardless of score; breaking ties by order;
adding a term-weighting scheme or embeddings inside this repo (roadmap item 1 —
and the floor does not move even then, because removing it would reintroduce
the confident-wrong-article failure at higher fidelity).

**Answers the question.** What does it do when the knowledge base doesn't cover the question? Why did it
answer with the wrong article?

**Lives in.** `tools.py (MIN_KEYWORD_MATCHES, search_policy, _tokenize), profiles/bookly.json (articles), transcripts/policy-answered-then-missed.json, deck/build.js slide 4`

---

## 10. Unified not-found wording — an order id must never become an oracle

*Phase 1*

**Decision.** An order that does not exist and an order that belongs to someone else produce
a byte-identical reply. ORDER_NOT_OWNED_BY_CUSTOMER gets its own reason code
and escalates internally on the back channel, and that branch deliberately does
not offer a human agent.

**Why.** Distinguishable replies would turn a guessed order id into an oracle for
whether that order exists and who it belongs to. Internally the distinct code
still routes it to a person in case it is a genuine account problem — the
escalation travels on the back channel, never in the reply. This exception is
later re-affirmed as the reason the narration rubric leaves escalation
ungraded: encoding it there would mean the grader reading reason codes and
holding opinions about what they imply.

**Rejected.** A more helpful, more specific error message telling the customer the order
belongs to another account.

**Answers the question.** Why is that error message so vague? Can I enumerate order ids by watching the
errors?

**Lives in.** `policy.py (decide_return, REASON_CODES), llm.py (_escalation → _order_not_found), rubric.py (MUST_OFFER)`

---

## 11. Reads may fall back; writes never do

*Phase 1*

**Decision.** Reads resolve by explicit reference → unique title match → order under
discussion → only one in transit → most recently ordered, and every read
records which rule got there. Writes resolve only on an explicit id or a
strongly-named title; no match asks, and more than one asks.

**Why.** A wrong read is cheap and self-announcing, because the reply always names the
order it describes. A wrong write moves money and announces nothing. The
asymmetry is deliberate, and recording the resolving rule matters because 'it
used the order already under discussion' and 'it fell back to the most recent
one' look identical in the reply and are very different facts. A correction is
recorded: the fallback argument holds for an *under-specified lookup* ('where
is my order' with three open) and does not hold when the customer asked a
*different question* that was silently coerced into a lookup — in that case
nothing escalates, nothing declines, and no reason code is produced, because
from the state machine's point of view nothing went wrong.

**Rejected.** One shared resolution strategy for reads and writes; applying the write-
strength rule to reads too, which would add clarifying turns for no safety
gain; removing the read fallback, which remains correct for reads.

**Answers the question.** Why is it willing to guess sometimes and not others?

**Lives in.** `agent.py (_resolve_read_target, _note_read, _handle_return), README.md 'Assumptions and limits', transcripts/order-history-and-identity.json`

---

## 12. A write acts on a title only when the customer actually named the book

*Phase 3*

**Decision.** `policy.title_reference_is_strong(matched, distinctive)` gates title-resolved
writes: one distinctive word suffices, otherwise at least
MIN_TITLE_WORDS_FOR_WRITE = 2 matched words. Generic words also stop nominating
candidates. The threshold lives in policy.py beside should_clarify;
`catalog.generic_words` lives in the profile. A weak reference is neither an
error nor an ambiguity — it asks exactly the question it would have asked if
the customer had said nothing, and records the refusal on the deterministic
side of the trace.

**Why.** The pre-existing guard protected against *ambiguity* (no match asks, two
matches ask). One match arrived at by coincidence is not ambiguous — at the
point of decision it is indistinguishable from the customer having typed the
title. It is a confidently wrong write, the failure this repository exists to
prevent, and naming that distinction is what made the fix findable. Two
corrections are on the record. The issue was originally filed as *latent*, safe
until the catalog grew; it was not — on the shipped five-order v2.0.0 dataset,
'return the left one' resolved The Left Hand of Darkness and 'the things I got'
resolved The Design of Everyday Things. No money moved only because those two
orders happened to be un-refundable: luck, not design. And the first cut of the
fix stopped generic words *confirming* a write but still let them build the
candidate set, so 'the Escher book' dragged in every title containing 'book'.
Placement is the other half: the rule is the guarantee, the list is the
readable knob — if the curated list is wrong the rule still holds, and a data
file must not be able to reach a money threshold. Cost measured and documented
rather than left to be discovered: 1 of 37 titles ('The Book of the New Sun')
has no long word that is not generic and always needs the clarifying question —
one extra turn, in the safe direction. That title was then deliberately
excluded from the catalog, because shipping a book the agent cannot be told
about would be a strange thing to do on purpose.

**Rejected.** Relying on the existing count-based ambiguity guard; a generic-word list alone
with no strength rule (the list is curated and therefore fallible, so the
guarantee must not rest on it); putting the threshold in the extractor or the
word list in code; special-casing the one unreachable title.

**Answers the question.** Can a coincidental word match move money? Has a decision in this build ever
been wrong?

**Lives in.** `policy.py (MIN_TITLE_WORDS_FOR_WRITE, title_reference_is_strong), profiles/bookly.json (catalog.generic_words), agent.py (_names_the_order), llm.py (_title_words), tests.py::a_coincidental_title_word_never_moves_money, README.md, commit c2732ac`

---

## 13. 'Returnable' has exactly one definition, and the agent offers only what it would grant

*Phase 3*

**Decision.** `policy.returnable_now` filters candidates by running them through
`decide_return` and keeping the approvals. The clarifying question offers only
those (minus anything already refunded this conversation). An out-of-window
order stays perfectly reachable by name and gets the same denial with the same
reason code.

**Why.** A second definition of 'returnable' would be a second place the answer could
change, and the whole architecture is one place per answer. Offering a customer
a book and then refusing it is the confidently unhelpful move: it costs them a
turn to be told no. This was forced by data growth — with 34 delivered orders,
'I'd like to return a book' produced a 1,070-character sentence listing thirty-
four titles, and the old rule ('everything delivered') was only ever survivable
because the dataset had two. The narrowing is presentation, not a new
eligibility rule, which is why hiding an order was never an option: the
customer must still be able to ask and get a reasoned no.

**Rejected.** A cheaper local status/date filter or a separate 'is it worth offering'
heuristic in the agent — a second source of truth.

**Answers the question.** How do you know the menu it offers matches what it will actually approve? What
does it do for a customer with 37 orders?

**Lives in.** `policy.py (returnable_now), agent.py (_start_return), README.md 'Assumptions and limits'`

---

## 14. Questions with nowhere to land get their own intent — and the general class stays open

*Phase 3*

**Decision.** `order_history`, `agent_identity` and `refund_status` are first-class intents,
added to both the regex stand-in and the hosted extraction prompt. Order
history answers with the count, then three most recent by name, then an offer
to name any of the rest; the preview count of three lives in agent.py,
explicitly not among the policy constants.

**Why.** With no home for 'how many books have I ordered', the two providers failed in
opposite directions: the stand-in extracted no intent and fell to the generic
help reply (clumsy but honest), while the hosted model mapped it onto
order_status — the only order-shaped intent available — and the read resolver
correctly fell back to the likeliest single order. The customer got a fluent,
confident answer to a question they did not ask, and nothing escalated and
nothing declined, because from the state machine's point of view nothing went
wrong. The advertised escalate-on-uncovered failure mode could not fire — not
because the guard was broken, but because the guard was never reached. An
intent surface is a boundary too, and a door only helps if the model knows it
is there, hence both extractors. The preview count is presentation because no
decision reads it, and reading thirty-seven titles back would be the same
mistake the clarifying question used to make with thirty-four. The record also
keeps the limit: this closes two instances, not the class. The gap was never
that the agent was wrong — it is that it could not tell it was out of scope,
and you do not solve that by adding intents one at a time until you run out of
customers. Deck slide 5 had to be corrected once the fix landed, because it was
still arguing from a defect that no longer existed; the replacement claim (the
door exists, the class does not) is the stronger one anyway.

**Rejected.** A catch-all fallback intent or a lower confidence threshold — a fallback cannot
fix an agent that cannot tell it is out of scope. Also rejected: answering
identity with a knowledge-base article. That was tried, probed and reverted:
the article needs 'you' and 'your' as keywords to clear the retrieval floor,
and with those keywords 'how long do you keep your records?' retrieves the
identity article — the confident-wrong-article failure the floor exists to
prevent, reintroduced. No answer is worth reopening it.

**Answers the question.** What happens when a customer asks something you didn't model? You said
uncovered cases escalate — why didn't that happen here?

**Lives in.** `agent.py (_answer_history, _answer_identity, HISTORY_PREVIEW), llm.py (VALID_INTENTS, extraction prompt), transcripts/order-history-and-identity.json, deck/build.js slide 5, docs/wiki/Home.md`

---

## 15. Asking about a refund is not asking for one, and the agent remembers what it already refunded

*Phase 3*

**Decision.** REFUND_STATUS_RE is tested before RETURN_REQUEST_RE. `self.refunds` sits
alongside `self.denials` in the conversation tier: an order refunded this
conversation is not offered as a candidate, and asking to return it again
reports the existing refund — in different words from the timing question.

**Why.** Every phrasing of asking *about* a refund contains the word the return regex
looks for. RETURN_REQUEST_RE matched the bare word 'refund' behind a single
negative lookahead for 'polic', so 'when will the refund show up?' routed to
the return handler, found no order reference and re-offered the returns menu —
moments after the agent had issued the very refund being asked about. Ordering
resolves it structurally; a better lookahead would have been an arms race
against phrasing. The double-refund framing was recorded precisely, because the
imprecise version invites the wrong fix: no money moved twice, since both
envelopes carry the same key and a receiver posts one write — the idempotency
contract was doing its job. What was wrong was a second `emitted` line in the
audit trail and an agent saying 'Done, I've issued a refund' for something it
did three turns earlier. `policy.returnable_now` is right and unchanged: it
judges the record, and the record has not changed because the agent emits and
does not execute. `_Turn.finished_orders` already enforced one decision per
order per *turn*; there was simply no conversation-tier equivalent. The
differing wording applies the repeated-answer lesson: two different questions
must not get one identical sentence.

**Rejected.** Extending the negative lookahead to cover question forms; changing
returnable_now to consider emitted-but-unexecuted refunds; mutating the order
record to 'returned'.

**Answers the question.** Why doesn't the policy engine know about the refund it just issued? Could a
customer get refunded twice by asking twice?

**Lives in.** `llm.py (REFUND_STATUS_RE, intent ordering), agent.py (Agent.refunds, _answer_refund_status), transcripts/refund-then-asking-about-it.json`

---

## 16. Three memory tiers, and an explicit intent-switching precedence rule

*Phase 1*

**Decision.** Turn memory is the `_Turn` dataclass; conversation memory is the Agent object
(pending question, clarify attempts, focus order, denial counts, issued
refunds); customer memory is the store. With a question pending: a turn that
answers the asked-about slot is a continuation; a turn that answers nothing and
names a different intent abandons the half-filled procedure and says so; a turn
that does neither gets bounded re-asks. Asking about an order is never choosing
it, the clarifying question is deferred to the end of the turn, and no order is
decided twice in one turn.

**Why.** Collapse the tiers and the second request of a conversation breaks — an earlier
turn's slot survives into this turn's decision. The precedence rule is the
state machine's heart: without it, 'Where's my Dune order?' spends one of the
two bounded clarification attempts and pushes the conversation toward
escalation, and worse, a question that happens to name a book reads as
selecting it for a write. Abandoning a half-filled procedure beats trapping the
customer in it; restating the intent later simply starts it again. Deferring
the question matters because a later request in the same turn may answer it,
and because if a later request completed the return the vague ask was the same
request restated and the question would be stale. Every branch records which
one it took, because they are invisible in the reply.

**Rejected.** A single mutable state bag across turns; treating any request carrying an order
reference as an answer; refusing to leave a procedure until it completes;
asking the clarifying question immediately at the point ambiguity is found.

**Answers the question.** Isn't three tiers of memory over-engineering? What if the customer changes the
subject mid-return?

**Lives in.** `agent.py (_Turn, _continue_or_switch, _is_answer, _defer_which_order, _ask_deferred_question, _finish_return), READING_GUIDE.md §3, deck/build.js slide 2 and slide 4`

---

## 17. Published commitments travel as facts on the event, never as strings in a template

*Phase 3*

**Decision.** `service_levels.refund_posting` and `service_levels.escalation_first_response`
live in the profile and are attached as facts on the narration event. Templates
render them conditionally; remove the service level and the reply stops rather
than inventing a timeframe. Both are also published as knowledge-base articles
so the question can be asked at any point.

**Why.** The narration system prompt forbids a hosted model from adding facts, amounts,
dates or promises the event does not carry. So a template literal works on the
stand-in and silently fails on the hosted path — the two providers would tell a
customer different things about when their money arrives, which also
contradicted the README's claim that both providers decline in the same words.
Carrying it as a fact means the hosted narrator is *looking it up* rather than
producing it. The issue's own diagnosis is preserved as a correction: it was
filed as a must-not-invent violation (the template asserts a number no fact
carries), and that was not quite right — 'within 5 business days' is a
published commitment the agent *should* state. But that is exactly why it could
not remain a template literal. The principle: stating a fact you were handed is
the right side of the line; asserting a number from a template is not.

**Rejected.** Hardcoding 'within 5 business days' in the stand-in template (shipped first,
then corrected); deleting the promise entirely, which loses a real commitment.

**Answers the question.** Where did that promise come from? You say both providers say the same thing —
do they?

**Lives in.** `profiles/bookly.json (service_levels), agent.py (_emit_refund, _emit_escalation), llm.py, store.py (SERVICE_LEVELS)`

---

## 18. The persona lines were struck, not made conditional

*Phase 3*

**Decision.** Reversal. The agent's self-introduction and its 'I'm sorry Dave, I can't do
that.' refusal catchphrase — both shipped deliberately in phase 2 — were
removed, and `_refuse()` was deleted rather than left dormant. What remains is
`agent.persona` in the profile, reaching only the narration prompt, which now
explicitly instructs the model *not* to introduce itself or prefix a speaker
label, and to write dates the way a person says them.

**Why.** The interface already names the speaker, so an agent that announces itself
every few turns reads as one with no memory — a claim this repo makes about
*other* architectures. A line prefixed to every refusal reads as a template
firing rather than an agent talking; the 2001 line is meant to land once, and
told three times in four turns it is a loop. A persona that fires on a loop is
not a persona, and dropping the catchphrase dropped no reason — a refusal now
says plainly why. Removing the instruction was not enough on the hosted path:
'you introduce yourself as Hal' is something a model does every turn, producing
'Hal · model' in the interface followed by 'Hal:' in the prose — the name twice
in two registers. The negative had to be stated. The stand-in could not exhibit
this because `_kb_answer` has no persona hook, so it was a hosted-only defect.
Dates likewise: the event carries `eta` as an ISO string because that is what
JSON carries, and a customer should not be reading database values.

**Rejected.** The proposed fix — conversation-tier `introduced`/`first_refusal` flags carried
as narration facts so the line lands once — rejected as machinery to manage a
line better removed. Leaving `_refuse()` dormant was also rejected: dead code
that still looks live. Post-processing model output to strip speaker labels.

**Answers the question.** Why does it keep saying its own name? Why does your agent sound like a loop?

**Lives in.** `llm.py (_narration_system_prompt), profiles/bookly.json (agent.persona), agent.py, tests.py::the_agents_voice_is_data_and_never_reaches_a_decision`

---

## 19. A follow-up is answered as a follow-up — and the original diagnosis was corrected

*Phase 3*

**Decision.** `already_discussed` is set on status facts when the read resolved via order-
under-discussion, and the templates phrase the same facts as a continuation
('Still on track — …') rather than repeating the previous sentence verbatim.
Wording only; no verdict reads it.

**Why.** The issue framed byte-identical replies to 'Where's my Dune order?' and 'When
will it arrive?' as a defect to be fixed by making the second reply
*different*. That diagnosis was wrong and the correction is kept: two identical
questions have one identical answer, and varying the content would be the worse
reply. The machinery was working exactly as designed — the follow-up carries no
order reference and still resolves to BK-1041 through conversation memory. The
customer simply could not tell, which is a presentation problem rather than a
correctness one, and generating variation would trade a legible defect for an
illegible one. The resolution rule travelling to the narrator as a fact is the
same shape as `response_target` on the escalation event: something the
deterministic side already computed, handed over so both providers can use it.

**Rejected.** Making the follow-up materially different in content ('lead with the answer'),
as originally proposed; having the narrator infer conversational continuity
from the transcript.

**Answers the question.** Why did your agent give the same sentence twice?

**Lives in.** `agent.py (_answer_status), llm.py (_status_report), transcripts/order-status-and-followup.json`

---

## 20. The rules-based stand-in is the default path, and the hosted default is a mini tier

*Phase 1*

**Decision.** With no vendor key set, `make_provider` returns RulesProvider; hosted models
are opt-in. Hosted defaults are claude-sonnet-5 and gpt-5.4-mini (moved down
from gpt-5.5), both overridable by BOOKLY_ANTHROPIC_MODEL /
BOOKLY_OPENAI_MODEL.

**Why.** The demo, the checks and CI must be runnable by a sceptic in ten seconds
without a billed account, and a green run has to be a green run of the
dependency-free path rather than of somebody's credits. It works on a plane.
The stand-in is serviceable because both model jobs are narrow and structured —
turn to slots, event to sentence — which is the same property that makes a mini
tier sufficient: a model that had to reason about eligibility would need the
frontier tier; one that only reads and phrases does not. The model choice is
itself evidence for the architecture. Env-overridable model ids mean a renamed
model is a one-line env fix rather than a code change. The stand-in's limit is
stated rather than hidden: it has a fixed phrase vocabulary, and turns it
cannot classify fall to a safe help reply rather than a guess.

**Rejected.** Requiring an API key to run anything; defaulting to the strongest available
model.

**Answers the question.** Can I just clone this and run it? Isn't the rules stand-in just a fake demo?
Which model does this need?

**Lives in.** `llm.py (make_provider, ANTHROPIC_MODEL, OPENAI_MODEL), harness.py (DEFAULT_PROVIDER), README.md`

---

## 21. An ambiguous provider environment is refused, not resolved by precedence

*Phase 1*

**Decision.** Reversal. Provider selection originally fell back to a vendor precedence order
when both keys were exported; now two keys set with no BOOKLY_PROVIDER raises.
VENDOR_KEYS ordering is presentation only.

**Why.** Silent precedence meant BOOKLY_OPENAI_MODEL was being set on a provider that
never ran — a demo running on a provider nobody chose. Two keys set is a
question, not a default: the same rule the agent applies to a customer turn it
cannot resolve, and the reason precedence was the wrong call here. Refusing
surfaces the ambiguity at startup rather than at the first turn.

**Rejected.** Vendor-key precedence order — the original shipped behaviour, explicitly
reversed.

**Answers the question.** What happens if I have both keys set?

**Lives in.** `llm.py (_provider_from_keys, VENDOR_KEYS)`

---

## 22. One hosted base class; a vendor supplies a name and a network call, and is verified before it becomes active

*Phase 1*

**Decision.** HostedProvider owns prompt construction, JSON parsing and untrusted-output
validation; AnthropicProvider and OpenAIProvider differ only in `_complete`.
`verify()` makes the smallest real completion through that same `_complete`
path before a provider is accepted, and a provider that fails never becomes
active. Both SDK clients carry an explicit 20-second timeout. The OpenAI
provider probes `max_completion_tokens` once and remembers, with a 4000-token
budget.

**Why.** There is no per-vendor copy to drift and, more importantly, no seam where a
vendor-specific code path could introduce a decision — that is the whole reason
swapping providers cannot change a decision. Verification exists because
constructing a client is entirely offline for both vendors, so a wrong key, a
renamed model and a missing network all looked like a successful switch and
only failed on the first customer turn, which on stage is the worst possible
moment. Going through `_complete` exercises key, model name, parameter shape
and network, not just whether credentials parse. The timeout is a stage-failure
mitigation: vendor defaults are minutes long, and a hung call is
indistinguishable from a broken one from the audience's seat. The parameter
probe exists because the rename split the model line, so neither name is
universally right; the larger output budget because on current models that
allowance also covers reasoning tokens, and too small a cap spends the whole
budget reasoning and returns empty content rather than an error — a failure
that does not look like one.

**Rejected.** Independent per-vendor provider classes; treating successful client
construction as validation; vendor default timeouts; pinning one output-budget
parameter name. Also corrected: the contract test originally counted class
members and broke when an unrelated transport helper was added — it was
rewritten to assert the invariant (a subclass never overrides prompt, parsing
or validation) rather than the shape.

**Answers the question.** Is this really provider-agnostic or just Anthropic with a wrapper? What happens
on stage if the key is wrong?

**Lives in.** `llm.py (HostedProvider, verify, HOSTED_TIMEOUT_SECONDS, OpenAIProvider._complete), web.py (set_provider), harness.py, app.py`

---

## 23. Model output is untrusted and validated down to the stand-in's exact shapes

*Phase 1*

**Decision.** `_clean_request` drops any intent not in VALID_INTENTS, any order id not
matching the BK-0000 pattern, any non-int option number (explicitly excluding
bool), and lowercases title words. Unparseable JSON yields one empty request.
Extraction's title vocabulary is scoped to the signed-in customer's own orders.

**Why.** An empty request means the agent asks rather than acting on a guess — failing
toward the clarifying question is the safe direction. Validating to the stand-
in's exact shapes is what makes provider parity a structural claim rather than
a hope: there is one downstream contract, and everything must conform to it
before it is seen. Scoping the title vocabulary to the current customer is why
a question about someone else's book reads as title-less rather than resolving
against another customer's record.

**Rejected.** Trusting the model's JSON; falling back to a best-guess intent on unparseable
output.

**Answers the question.** What if the model returns garbage? Can one customer's question surface another
customer's data?

**Lives in.** `llm.py (_parse_requests, _clean_request), tools.py, tests.py::hostile_model_output_cannot_reach_a_decision`

---

## 24. The API key is passed explicitly and never exported to the environment

*Phase 2*

**Decision.** Hosted providers take an explicit `api_key` argument. The console holds it in
one variable on one object — never on disk, never logged, never in a URL, never
in os.environ — and shows only a four-character tail. The check-suite
subprocess environment is built with every vendor key stripped out.

**Why.** The console spawns a subprocess to run tests.py, and an exported key would ride
into it. The explicit-argument design exists specifically so nothing has to be
exported. Stated as the right shape for a demo and explicitly not a secrets
manager.

**Rejected.** `os.environ["ANTHROPIC_API_KEY"] = key` — the obvious way to make the SDKs pick
it up.

**Answers the question.** Where does the pasted key go?

**Lives in.** `web.py (Console._api_key, checks_command, _mask), llm.py (build_provider), README.md 'Assumptions and limits'`

---

## 25. Parity proven by a real hosted run, the drift recorded, and later turned into a command

*Phase 3*

**Decision.** The same four-scenario script was run against the stand-in and gpt-5.4-mini.
All eight replies were worded differently; every decision field matched,
including the idempotency key and the $22.50. The hosted run also dropped the
offer of a human agent on the knowledge-base miss, and that was published
rather than omitted. `harness.py --provider` later made this reproducible:
every envelope field, reason code and key compared exactly, the verbatim reply
comparison skipped, and prose graded by the rubric instead.

**Why.** It converts the central claim from an argument into a result, and the key is
the instrument: an identical hash means the same action on the same order, so a
deduping receiver cannot tell which model ran and does not need to. An earlier
commit had honestly answered 'have you run this against a hosted model?' with
'no' when credits were unavailable — an unverified claim left standing in a
deck is worse than an admitted gap — and that honesty move is kept in the
record even though it was superseded two commits later. The volunteered
divergence is the load-bearing part: a reviewer who finds an unmentioned
discrepancy discounts everything else, and the artifact itself closes with a
named section stating what it does *not* prove — that the decisions are
provider-independent and the prose is not. That single observation is the
entire case for the graded rubric two phases later, and is the exact reply the
rubric is required to fail. Skipping the verbatim comparison on a hosted run is
a decision, not an omission: a hosted model wording things differently *is* the
parity claim.

**Rejected.** Publishing only the favourable half of the result; presenting the parity run as
a clean sweep; byte-comparing hosted prose against the template; leaving parity
as a hand-run artifact pasted into a file.

**Answers the question.** Have you actually run it against a hosted model? Did anything differ besides
wording? How do I reproduce your parity result?

**Lives in.** `evidence/provider_parity.txt, evidence/hosted_openai_transcript.txt, harness.py, deck/build.js slide 2`

---

## 26. The agent talks to a recorder that does nothing, and each stage's side lives in one table

*Phase 2*

**Decision.** `NullRecorder.note` is an empty method and is the default; the CLI passes no
recorder, the web layer passes a ListRecorder. `recorder.STAGE_SIDES` maps
every stage to MODEL or DETERMINISTIC in one dict. The `extract` stage is
tagged model-side regardless of which provider ran. Clarify is two notes on
opposite sides — whether to ask is deterministic, the wording is narration —
and must never render as one row. The customer's own words get no note at all.
Notes are deep-copied on the way in; an unknown stage records 'unknown' rather
than raising.

**Why.** The obvious alternative — having handle_turn build a payload for the UI — puts
presentation inside the orchestrator and makes the agent's behaviour depend on
who is watching. With a null default there is no branch anywhere asking whether
anyone is listening, which is why the CLI's output stayed byte-identical to
v1.0.0 through every commit of phase 2. One table means one place to review and
no way for a call site to mislabel itself; the interface colours by this tag,
so getting it right is a design decision rather than a formatting one. Tagging
the regex as model-side is the sharp case: the tag names the *seat*, not the
implementation, and colouring the regex purple would quietly claim it is
trustworthy in a way a hosted model is not — the opposite of the argument the
repo makes. Collapsing the two clarify notes would show a policy decision and a
piece of model prose as a single event, which is exactly the conflation the
boundary exists to prevent. The agent mutates some noted dicts afterwards, and
a trace that changes after the fact is not a trace. The recorder is a bystander
and must never be able to break a turn, so a typo'd stage fails loudly in the
suite instead of quietly on stage.

**Rejected.** Returning a UI payload from handle_turn; an `if recorder is not None` branch;
tagging each note at its call site; tagging by what actually ran; a third
'customer' side; raising on an unknown stage.

**Answers the question.** Did adding the GUI change the agent? The stand-in is deterministic — why is it
on the model side?

**Lives in.** `recorder.py (NullRecorder, STAGE_SIDES, side_of, ListRecorder._plain), agent.py, app.py`

---

## 27. The presentation layer computes nothing, and holds no copy of a threshold

*Phase 2*

**Decision.** web.py holds conversation state and serves records; turns go to the same
`Agent.handle_turn` the CLI calls. It owns exactly two things — which provider
is active and the session API key. policy.py publishes descriptive CONSTANTS
and REASON_CODES registries that nothing above them reads and whose deletion
would change no outcome; the interface and the back office both render
thresholds from them. `web_layer_emits_identical_envelopes` drives all four
scenarios through a real server on a real socket and through Agent directly,
comparing every decision field including the key.

**Why.** Answering 'did you just bolt a UI onto it' has to be mechanical rather than
rhetorical, and the idempotency key is the sharpest available detector: it
would diverge the moment the web layer started keeping its own conversation
identity. Naming the complete list of what the presentation layer may own is
what makes 'this layer decides nothing' checkable rather than aspirational. The
registries exist because an interface allowed to hold its own copy of '30' is
an interface that will eventually disagree with the engine — but they are
descriptive and inert, so the presentation layer gains no path into the
decision path. A check asserts every entry still equals the module attribute it
names, so drift fails loudly. Serving both surfaces from one function means the
two cannot disagree either.

**Rejected.** A framework-based front end with its own logic; hardcoding the displayed
thresholds in JavaScript; mocking the HTTP layer in the parity comparison.

**Answers the question.** Did you bolt a UI onto it, or is the UI deciding things? How do I know the
number on screen is the number the engine used?

**Lives in.** `web.py (Console, policy_json), policy.py (CONSTANTS, REASON_CODES, describe), backoffice.py, tests.py::web_layer_emits_identical_envelopes, tests.py::policy_constants_surface_matches_policy`

---

## 28. Human resolution is append-only, and nothing flows back into a verdict

*Phase 2*

**Decision.** A reviewer's action becomes a separate later event pointing at the original
with `supersedes`; the original verdict stays exactly as policy.py computed it.
`emit_resolution` is its own function, not an `actor` argument on `emit`. Actor
and justification are required, enforced once in queue.py. Both 'override' and
'uphold' are recorded. Cases are keyed on the escalation's idempotency key, and
snapshot their full context at open time. The agent is never told the queue
exists — the web layer watches what was emitted and lands it.

**Why.** If overriding rewrote the decision, the record would only ever show the last
opinion — which is precisely what makes giving a reviewer override authority
safe in the first place. The separate function is justified twice over: agent
envelopes stay byte-identical so the existing duplicate-receipt evidence
remains valid, and these are not the same kind of record — an agent envelope
carries a reason code because a policy function produced it, a resolution
carries a person and a sentence because a person did, and inventing a reason
code for a human judgement would be the dishonest field on the screen. An
override with nobody's name and no reason is exactly the artifact an auditor
cannot use, and a second copy of that validation rule in the browser is a rule
that can disagree with itself. Recording upholds too means the queue is a log
of review rather than a log of exceptions. Keying on the escalation key means
four pushes on one dispute make one case with four recorded events, keeping the
same guarantee the envelope already makes: delivery may repeat, but the key
means it is recorded once. Snapshotting means a
ticket read six weeks later shows what was true when it was raised rather than
silently re-reading a world that moved. And nothing flows back: a check asserts
by source inspection that the decision path imports none of the queue, then
overrides a denial and shows the next verdict on that order is identical — if a
human override could become an input to a later verdict, the engine would stop
being the only thing deciding.

**Rejected.** Mutating the case's verdict on override; an optional `actor` parameter on
emit(); client-side required-field validation as the gate; logging only
overrides; a new case per escalation envelope; live lookup at read time; giving
the agent a queue client or feeding prior overrides back as precedent.

**Answers the question.** Can a human quietly rewrite a verdict? Does a human override change what the
agent decides next time?

**Lives in.** `queue.py (resolve, ACTIONS, open_case, _case_id), envelope.py (emit_resolution), web.py (escalation_context, Console.turn), tests.py::queue_resolution_is_append_only, tests.py::back_office_returns_nothing_that_reaches_a_verdict`

---

## 29. The executing side runs in a second process, and every mock says it is one

*Phase 2*

**Decision.** backoffice.py runs on 127.0.0.1:8787, started separately, and is a drop-in for
the untouched stub_receiver.py — you run one or the other. Its ledger
deduplicates by idempotency key in memory, dying with the process, drawing a
repeat against the line it duplicates rather than as a second line, and a
permanent chip on screen states the durability limit. The policy viewer is
read-only and names who can change a constant and where.

**Why.** The separation is the architectural argument rather than packaging: if the
thing receiving envelopes ran inside the agent's process, 'the agent emits
rather than executes' would be a diagram instead of a fact you can check by
killing something. Demonstrated by killing the receiver mid-conversation — the
refund still decides at $22.50, still writes its audit line, and records
failed_unreachable. Sharing the stub's port makes interchangeability the point:
the same envelope contract serves both, so evidence captured against the stub
reproduces key for key. A second ledger line would be a second refund on
screen, which is the exact failure the key exists to prevent. Claiming durable
dedup here would misrepresent what the demo proves, on the one screen whose
whole job is the exactly-once claim — so every surface says what it is, on
screen, permanently, rather than requiring anyone to read a docstring. And the
read-only viewer is a refusal by design: making procedures authorable by non-
engineers is the next order of problem, and mocking it would be the one
dishonest thing on screen.

**Rejected.** An in-process receiver; replacing stub_receiver.py; persisting the ledger to
imply durability; a fake policy editor for demo effect. Also corrected:
printing arriving envelopes was made a property of the program rather than the
object, because a check that constructed a receiver was dumping envelope JSON
into the middle of the Checks panel.

**Answers the question.** What happens if the downstream system is down? Is that dedup durable? Can a
non-engineer change the rules?

**Lives in.** `backoffice.py (Ledger, STAND_IN_NOTICE, policy viewer), stub_receiver.py, DEMO.md §7, docs/wiki/Home.md`

---

## 30. A scenario is a file, not a function — and what the fixture pins is chosen carefully

*Phase 3*

**Decision.** `golden_transcript_return_flow` (one conversation with strings pasted into a
function body) was replaced by `transcripts/*.json` replayed by harness.py
through the same `handle_turn` the CLI and console call. Each fixture pins the
reply verbatim, the envelope decision fields including the *literal*
idempotency hex, the sequence of recorder stages, and which event kinds were
narrated. The side of each stage is deliberately not stored; it is read from
`recorder.STAGE_SIDES` at replay time. Replay is hermetic — BOOKLY_WEBHOOK_URL
is unset for the duration. tests.py generates one named check per fixture, and
the directory cannot be emptied.

**Why.** The old form was the right idea in the wrong container: the cost of a second
scenario was a second function, and nobody was ever going to pay it twice.
Coverage that expensive to add does not get added — and the claim was spent
immediately, with four more scenarios costing four files and no new test code.
The stage sequence is the architectural claim turned into a regression test: a
change that moved a decision to the model side of the boundary fails a test
rather than a code review. The literal hex matters because re-deriving the key
at check time passes even when the key material changes, since both sides of
the comparison move together; a literal fails loudly, which is what a golden
fixture is for. The side label is excluded so there is no second copy to drift
— the same reasoning as asserting `customer_note` against the turn's own text
rather than storing it twice. Hermetic replay because a golden transcript must
not depend on whether a receiver happened to be running, and a check run must
not post test envelopes into the ledger a demo is about to show — which, with
the console spawning the suite as a subprocess, is not hypothetical. Named
checks so the panel streams `transcript_return_with_clarification` rather than
`check_7`, and the emptiness check so deleting the fixtures cannot delete the
coverage and leave the suite just as green.

**Rejected.** Adding a second golden-transcript function; a test-only fast path through the
state machine; recomputing the key at check time; recording the side tag in
each fixture; omitting the always-constant delivery field.

**Answers the question.** How do you regression-test the conversation, not just the policy? What stops
someone quietly moving a decision into the prompt?

**Lives in.** `harness.py (replay, compare, ENVELOPE_FIELDS), transcripts/, tests.py, recorder.py (STAGE_SIDES)`

---

## 31. known_gaps and accepted are two lists, enforced in both directions, and blessing cannot launder either

*Phase 3*

**Decision.** A rubric finding must be acknowledged either as a `known_gap` carrying the
issue number that will close it, or as `accepted` carrying a written argument.
An unacknowledged finding fails; an acknowledgement the rubric no longer
reports fails as stale; one with neither issue nor reason is refused outright.
Acknowledgements carry an occurrence count. `--bless` regenerates turns from a
real run but copies both lists through untouched, and refuses to run against a
hosted provider.

**Why.** One list is a debt and the other is a decision, and a defect allowed to sit in
the wrong one never gets looked at again. The `accepted` path exists because
the rubric's own rules can be wrong — the repeated-answer episode proved it,
and an instrument with no way to record a reasoned disagreement either forces
bad fixes or gets silently disabled. Two-directional enforcement is what stops
the list becoming the place failures go to be forgotten: a fix has to delete
its own excuse. Counts mean a new defect cannot hide behind an old one.
Blessing not regenerating the lists matters because an acknowledgement is a
human's statement about an open defect, and regenerating it from a run would
let a re-bless quietly launder one. The residual hazard is stated rather than
designed away: blessing a regression is one command and a wrong expectation
looks exactly like a right one afterwards. There is no way to make that safe
inside the tool, so the mitigation lives outside it — the diff is the review,
which is one of the reasons the commit history is meant to be read. And a
fixture blessed from a hosted run would pin one sampling of one model's prose
as the repo's expected text, the opposite of what these files are for. By the
end of the branch every known_gap had been closed and deleted by the fix that
closed it.

**Rejected.** One combined exemption list or a plain ignore flag; suppressing findings;
fixing everything in the same commit as the fixture that found it; an
interactive confirmation on bless; allowing hosted blessing with a warning.

**Answers the question.** Isn't 'accepted' just a nicer word for suppressed? What stops that known-gaps
list becoming a graveyard? What stops you blessing a bug?

**Lives in.** `harness.py (_compare_rubric, bless, main), transcripts/repeat-question-same-answer.json, transcripts/refund-then-asking-about-it.json, transcripts/order-history-and-identity.json`

---

## 32. The rubric is handed exactly three things, grades mechanically, and had to catch something real

*Phase 3*

**Decision.** rubric.py receives per narration only the event kind, the facts the agent
already gave the narrator, and the text that came back — no Order, no Verdict,
no policy/store/tools import, asserted by grep in both directions. Four
mechanical rule families: must_carry (matched on canonical tokens, so 'August
1' satisfies '2026-07-18' and '$22.50' satisfies 22.5), must_offer (a phrase
class, not an exact string), must_not_invent (no unsupported number, no ISO
date, no speaker label — matched name-agnostically), repeated_sentence across a
whole conversation. A check grades the recorded gpt-5.4-mini kb_miss reply —
quoted verbatim and asserted still present in evidence/provider_parity.txt —
and requires the rubric to fail it, while the template's own miss grades clean.

**Why.** It cannot judge whether a refund was correct because it is never told what the
refund was — starving it of decision inputs is what makes it structurally
incapable of becoming a second decision-maker. The boundary is self-policing:
if a rule here ever needed a record or a threshold to do its job, that would be
the signal grading had turned into deciding, and the rule would be wrong rather
than the boundary. Findings are inert and travel outward exactly like a queue
resolution, demonstrated behaviourally — the same conversation replayed with
and without a grading pass returns identical replies and envelopes. Rules are
mechanical because an LLM judge would put a model back in the seat this whole
repo exists to keep it out of, make the harness nondeterministic and network-
dependent, and mean a regression check that costs money and needs a network —
which is a check that gets turned off. Token and phrase-class matching lets a
hosted narrator pass in its own words while still failing on a dropped fact:
the failure being graded is the dropped commitment, not the wording. Two
scoping decisions are named rather than left as oversights. `kb_answer` is
excluded from must_carry because its whole fact is a paragraph of article copy,
and requiring a paraphrase to contain it would be an exact-string test wearing
a rubric's clothes. And `escalation` is deliberately ungraded by must_offer,
because the ORDER_NOT_OWNED_BY_CUSTOMER branch must *not* offer a human or a
guessed order id becomes an oracle — encoding that exception would mean reading
reason codes and holding opinions about what they imply. A rubric that has
never caught anything is a proposal, so it had to fail a real recorded hosted
reply, offline and unbilled, before being trusted; grading the clean miss too
proves it is not simply failing everything. The drift it catches moved no
verdict, no reason code and no envelope field — prose is the only place that
failure exists and the only thing the customer reads. A known limit is stated
rather than papered over: the refund promise was caught because a number is
easy to trace, and a commitment phrased without a number would slip past.

**Rejected.** An LLM-as-judge grader; giving the rubric the order record so it could grade
correctness; exact-string matching for must_offer; adding escalation to
MUST_OFFER with a reason-code exception; a general 'detect any promise' or 'is
this well-phrased?' rule, which would require semantic judgement and turn the
grader into a style checker for one provider's phrasing.

**Answers the question.** Isn't the grader just another model making decisions? Why not use a model to
grade the model? Has your rubric ever actually found anything?

**Lives in.** `rubric.py (MUST_CARRY, MUST_OFFER, _must_not_invent, grade_transcript, SPEAKER_LABEL_RE), tests.py::the_rubric_cannot_reach_a_decision, tests.py::the_rubric_catches_the_recorded_hosted_drift, evidence/provider_parity.txt`

---

## 33. The documented check count and the CI workflow's own constraints are asserted by the suite

*Phase 3*

**Decision.** The number is `len(CHECKS)`. `documents_state_the_actual_check_count` holds a
registry of documents citing it, reads each, and fails by file, line and stale
number — and a document that *stops* citing the count also fails. Claims are
standardised to numerals with a tight pattern.
`the_regression_run_installs_nothing` asserts the workflow contains no pip
install, no npm, no vendor key and no `secrets.` reference, and that the
promised interpreters (3.9, 3.13, 3.14) are actually in the matrix, with fail-
fast disabled. A second job boots a clean clone, reads /api/customer, and walks
demo.txt through the CLI.

**Why.** DEMO.md told the presenter to say 'forty-five checks' while fifty streamed past
on screen; the number was hand-written into seven places across five files and
kept honest by memory. It is the same argument the policy-constants check
already makes about thresholds — a document holding its own copy is the same
failure with a slower fuse — and generation was unavailable because the no-
build-step constraint does not move, so the registry *is* the centralisation.
Failing on a removed citation stops the check being made vacuous by deletion.
The check earned its keep unprompted as the count moved 51 → 52 → 56 → 68 → 69 → 74 → 75 → 78 → 80 → 81 → 82,
naming the files to update each time. CI matters because every claim about the
suite being green was a claim about somebody's laptop, and slide 5 argues that
agents die from silent regressions when somebody tweaks a prompt on a Thursday
— a suite nobody runs on Thursday does not answer that. Dropping 3.9 from CI
while the README promises it is the same class of drift as DEMO.md saying
forty-five. And the absence of an install step is asserted rather than merely
observed, so the dependency-free proof is not itself unguarded. One caveat is
on the record: evidence/test_output.txt is a captured run listing 50 checks and
is stale against the current count. It is a dated snapshot rather than a claim,
and no check reads it — but a sceptic comparing it to the deck will notice.

**Rejected.** Fixing the seven copies and remembering harder; a build step or doc generator;
allowing spelled-out numbers; default fail-fast; a single-interpreter matrix; a
requirements.txt for convenience.

**Answers the question.** Your README claims fifty checks — who checks the README? Does this actually run
in CI? How do I know it really has no dependencies?

**Lives in.** `tests.py (DOCUMENTS_CITING_THE_COUNT, documents_state_the_actual_check_count, the_regression_run_installs_nothing), .github/workflows/checks.yml, DEMO.md, README.md, docs/wiki/Home.md, evidence/test_output.txt`

---

## 34. New checks are proven by breaking them

*Phase cross-cutting*

**Decision.** Each new instrument was demonstrated failing before it was trusted: the doc-
count rule by setting DEMO.md back to 45; the transcript harness by changing
'Done —' to 'All set!' in one template; the rubric by grading a real recorded
hosted reply; the CI workflow by running it rather than merging and assuming.
The suite itself asserts it never reaches a hosted provider — and asserts the
default is the stand-in *and not merely named after it*, by checking a replayed
reply is byte-identical to what the template produces directly.

**Why.** A green check that has never gone red is an untested check — a proposal, not a
guarantee. Each of these establishes that the instrument can actually fail
before it is trusted to say something passed. The offline assertion is the same
instinct pointed at the suite's own defaults: checking the name of the default
would not catch a default that had been rewired underneath, and a green run
must provably not be a billed one.

**Rejected.** Asserting the instrument works because the suite is green; trusting the default
flag value.

**Answers the question.** Does that check actually catch anything? Does your test suite cost money to
run?

**Lives in.** `tests.py (the_suite_never_reaches_a_hosted_provider), harness.py, rubric.py, .github/workflows/checks.yml`

---

## 35. Envelope identity is the standing regression invariant

*Phase cross-cutting*

**Decision.** Nearly every commit ends by asserting the demo script's envelopes are byte-
identical to the previous tagged release — field for field, both idempotency
keys included — including across a dataset that grew seven times larger.

**Why.** It is the one-line proof that a change was voice, presentation, or coverage and
not a decision change. It is what lets a commit that rewrites the persona, adds
32 orders, or adds two intents be reviewed as not touching money, and it is
what turned the 7× data growth from a risk into a checked fact.

**Answers the question.** How do I know this refactor didn't change a decision?

**Lives in.** `commit messages across the branch; transcripts/hero-sequence.json; evidence/`

---

## 36. The dataset is a profile; thresholds and reason codes deliberately are not

*Phase 2*

**Decision.** Customer record, orders, knowledge base, catalog, frozen clock, scenarios,
suggested prompts, agent voice and service levels live in
`profiles/<name>.json`, selected by BOOKLY_PROFILE and loaded at import into
the same frozen dataclasses. Policy thresholds, reason codes and the provenance
palette stay in code. Suggested next prompts are keyed by the reason code the
turn produced, and a check asserts each is a turn the agent actually handles.

**Why.** Standing this up for another company must be a data edit measured in minutes
rather than a code edit, and nothing downstream knows the data moved. But a
profile may change what the agent *says* and never what it *decides* — that
line is the whole test for whether something belongs in data, and a data file
must not be able to reach a threshold or invent a reason code. The palette
stays out for the same reason: purple/grey is the design system carrying
provenance meaning, not a brand choice a re-skin gets to break. Suggestions
live in the profile so re-skinning stays a data edit, and none of them carries
a hint about the answer — a suggestion that dead-ends is worse than no
suggestion, and one that staged the answer would be staging the demo. Order
insertion order is load-bearing and preserved, because the clarifying question
numbers its options in the profile's order.

**Rejected.** Moving everything configurable, including thresholds and reason codes, into the
profile; hardcoding the dataset in store.py; hardcoding suggestions in the
front end.

**Answers the question.** How long would it take to stand this up for our data? What can a non-engineer
change without a code review?

**Lives in.** `profiles/bookly.json, store.py (load_profile, build_orders, TODAY, AGENT, SERVICE_LEVELS), policy.py, tests.py, README.md 'Re-skinning'`

---

## 37. 37 vs 5 orders: the store was made to match the record, after weighing the alternative

*Phase 3*

**Decision.** `customer.orders_placed` said 37 while the store held 5, both on screen for the
whole demo. Two coherent fixes were weighed: set the record to 5, or keep 37
and make the excerpt explicit. Option 2 was preferred and shipped by populating
the store with 32 more titles so record and store genuinely agree at 37, dated
across two years. Exhalation was deliberately dated inside the return window so
the clarifying question still presents a real choice.

**Why.** Nothing decides on either number, so no verdict changed — it was a credibility
bug. But it becomes a correctness problem the moment the agent can answer 'how
many books have I ordered': the answer would have to come from one of two
defensible, mutually exclusive numbers, and an agent that says 37 and can then
only discuss five of them is the confidently-wrong-sentence failure one level
up. Option 2 won because 'the store is an excerpt' is true and is the kind of
limit this repo states out loud elsewhere, and because setting the record to 5
discards the 'real customer with a real history' texture. The dataset content
was then chosen to preserve the demo's decision points: without a second in-
window candidate the clarifying question would resolve to one option and refund
without asking. Sequencing was imposed explicitly — the write-path title guard
had to land *before* the catalog grew, because populating the store is what
surfaced that bug and shipping the data first would have put a money-moving
defect into the demo dataset.

**Rejected.** Option 1 — lowering the record card to 5 — defensible on the grounds that a
demo dataset should not need a footnote, but it discards the texture. Landing
the catalog before the write guard.

**Answers the question.** Your customer card says 37 orders and I count five rows.

**Lives in.** `profiles/bookly.json (customer.orders_placed, orders, catalog.titles), commits c2732ac → 4931f5d → 9dd8056`

---

## 38. The injection claim is bounded, and there is nowhere in the client for markup to be parsed

*Phase 2*

**Decision.** The stated claim: injection is inert on verdicts, amounts and reason codes, and
*not* on which requests get considered — an injected order id is judged like
any other request and may legitimately produce an escalation. Injected text
rides the envelope verbatim as `customer_note` and reaches the audit log
intact; nothing parses it. In the browser the markup-parsing APIs are absent
from the client entirely, verified by grep and live: an `<img onerror>` turn
produces zero img and zero script elements.

**Why.** QA caught an overclaim in the deck and slide 4 was retitled — the narrower
claim is the true one, and overclaiming a security property is the fastest way
to lose the room when someone tests it. Its blast radius is exactly the blast
radius of a normal customer request; that is the honest claim and it is the one
worth having. Preserving the injected sentence verbatim is deliberate: the
record of what the customer actually said is exactly what an investigator
needs, and preserving it is *how* you prove it changed no verdict. On the
client, 'escaped where needed' is a claim about vigilance that a new
contributor can break; 'the sink does not exist' is a claim about structure.
Reinforced later by the back office importing the console's element builder, so
exactly one function in the build puts text on a page. The SVG cover generator
escapes quotes as well as brackets uniformly for the same reason — the
invariant becomes one sentence rather than a per-context judgement, and the
check for it caught a real attribute break-out in aria-label.

**Rejected.** The original broader 'injection is inert' framing; sanitising input on the way
in; stripping injected text from the record to make a slide cleaner; escaping
at each insertion point; context-sensitive SVG escaping.

**Answers the question.** So injection can't do anything at all? What stops that injected string
executing in the browser?

**Lives in.** `static/app.js, backoffice.py, envelope.py (customer_note), covers.py, tests.py::injection_changes_nothing, evidence/injection_transcript.txt, deck/build.js slide 4`

---

## 39. Colour is provenance, and the console layout is the boundary diagram

*Phase 2*

**Decision.** Purple means the deterministic side, grey the model side, and the customer's
own words are neither — neutral with an outline. Record left, conversation
middle, a 3px full-height rule, decisions right: literally slide 1's drawing.
The rule uses its own token and is deliberately *not* purple. Purple is never
spent decoratively, which is why the deck's thesis headline and slide-1 divider
are neutral. Customer view re-renders the record without lifetime value or CSAT
rather than hiding them with CSS, and the console opens in customer view.
Motion is spent in exactly one place — trace rows streaming in pipeline order —
and prefers-reduced-motion turns it off.

**Why.** The interface argues the architecture without a caption: say the rule once and
no other colour needs explaining for the rest of the demo. Provenance colour is
load-bearing information, not branding, so a line belonging to neither side
must not be painted with the colour that means one side. Not rendering the
operator fields is the difference between a data-exposure claim you can make
and one you cannot — 'shows nothing a customer should not see' should be true
of the document, not just the paint — and opening in customer view means it
reads as a finished product before it gets peeled open. The one animated thing
is the thing being argued: the order the pipeline ran in. Two accessibility
failures were fixed at the ink level (grey at 3.69:1, purple at 4.30:1 on tint)
by darkening rather than changing hue, leaving the provenance meaning
untouched.

**Rejected.** Brand purple for the divider and as a general accent; `display:none` on the
sensitive fields; changing the hues to fix contrast.

**Answers the question.** What would the customer actually see?

**Lives in.** `static/, web.py, deck/build.js (PURPLE/GREY constants and MEANING comments), README.md 'The console', DEMO.md §2`

---

## 40. Evidence is run and captured with its method stated, and the deck argues by naming costs

*Phase cross-cutting*

**Decision.** Every empirical claim points at a file in evidence/ produced by an actual run,
each opening with the exact commands and the controls that make the comparison
valid. Deck transcript excerpts are copied verbatim with elisions marked and
re-verified whenever the agent's wording changes. Slide 3 presents every key
decision as CHOSE / GAVE UP / WORTH IT BECAUSE. A whole slide is spent on what
broke. The .pptx is generated by deck/build.js, never hand-edited, and deck/
declares itself build tooling that no Python module imports.

**Why.** A deck that claims things a sceptic would reasonably doubt needs a filename
rather than a reassurance, and an artifact whose method is not stated is
indistinguishable from a screenshot of a hoped-for outcome — stating the
controls pre-empts the smart objection in each case (that the keys matched
because nothing changed, or that the duplicate was the same process replaying
itself). A quoted transcript that drifts from the artifact is a fabricated
quote, so marking elisions lets a slide be short without being edited-to-
favour. A tradeoff you cannot name is not a tradeoff, it is a sales pitch — and
naming the cost first takes the interviewer's best move away. Bugs found and
fixed are the only credible evidence the system was exercised rather than
demoed, and the bugs chosen have lessons that generalise, so the slide argues
architecture while appearing to confess. Two evidence files captured before the
persona landed carry a dated provenance note rather than being re-recorded: a
reader comparing them to a live run would find wording that no longer matches
and reasonably suspect editing, and the note turns the mismatch into a second
demonstration of the thesis — the persona changed the prose and moved nothing
on the decision side. Generating the deck also puts speaker notes, excerpts and
layout arithmetic under review. A separate correction is recorded:
deck/build.js initially required pptxgenjs with no manifest, so the preceding
commit's claim that the .pptx is rebuildable was false from a fresh checkout; a
package.json and README were added and verified from a git-archive copy to
byte-identical slide XML. Layout defects were found twice by rendering and
looking, not by arithmetic — neither was visible in the source.

**Rejected.** Claiming parity and idempotency from the design without demonstrating them end
to end; a tidied illustrative transcript; re-capturing runs so the wording
matched; a benefits-only key-decisions slide; a polished deck with no failures
in it; hand-editing slides in PowerPoint.

**Answers the question.** Is that transcript real? What did this design cost you? What went wrong while
you were building this?

**Lives in.** `evidence/ (provider_parity.txt, duplicate_receipt.txt, injection_transcript.txt, audit_trail.txt, demo_transcript.txt, hosted_openai_transcript.txt), deck/build.js, deck/README.md, deck/package.json`

---

## 41. What is deliberately absent, each for a stated reason — and the authorship gap is conceded

*Phase cross-cutting*

**Decision.** No auth, no multi-tenancy, no database, loopback only. No supervisor agent. No
durable dedup. No policy-authoring surface. The review queue is a JSON file
both processes read, correct for one reviewer at one desk. The API key is
session memory and not a secrets manager. queue.py knowingly shadows the stdlib
and its docstring names what breaks. The console is deliberately kept off the
roadmap slide, which argues embeddings behind the retrieval floor, the
unmodelled-question class, and a real orchestration layer.

**Why.** Naming what the demo does not do is the same instinct as the read-only policy
viewer: state the limit rather than let it be discovered. Each omission is
scoped rather than overlooked — the queue's correctness claim states its own
concurrency, and a known hazard documented with its trigger and its remedy is
safer than one silently worked around, which also stops a future contributor
inventing a hack. The console is off the roadmap because that slide argues
correctness and durability and a presentation-layer item would dilute it: the
GUI is the medium, not the roadmap. And when asked about the resemblance to
Decagon's Agent Operating Procedures, the prepared answer concedes the
convergence rather than denying awareness or claiming equivalence, then names
the real gap: these procedures live in policy.py, so changing one takes an
engineer, where theirs are written by the CX teams who own the policy. Making
them authorable by non-engineers is the next order of problem and is harder
than what was built — which is exactly why mocking an editor here would have
been the one dishonest thing on screen.

**Rejected.** Shipping a mocked policy-authoring UI; renaming queue.py or working around the
shadowing at import sites; listing console features as roadmap items; not
mentioning the AOP resemblance and hoping it went unremarked.

**Answers the question.** What's missing, and what would you build next? Could a non-engineer change the
return window? This looks a lot like Decagon's Agent Operating Procedures — did
you know that?

**Lives in.** `README.md 'Assumptions and limits', docs/wiki/Home.md 'Deliberately out of scope' and 'Known future phases', queue.py, backoffice.py, deck/build.js slide 5, PR #12 body`

---

## 42. The catalog grew to 38 for a book the demo wanted, and the count moved with it

*v3.1.0*

**Decision.** *2001: A Space Odyssey* (Arthur C. Clarke) was added as a 38th order
(BK-2132) for the demo customer, dated a 2025 purchase — **delivered, but well
outside the return window**. `customer.orders_placed` moved 37 → 38 in the same
edit, the title joined `catalog.titles`, and the one count-bearing golden
transcript (`order-history-and-identity`) was re-blessed 37 → 38 / 34 → 35. The
earlier entries in this file that cite 37 orders, 34 delivered, or "1 of 37
titles" are left exactly as written: they record the state at the time those
decisions were made, the same way the dated provenance notes in entry 40 do.

**Why.** The status and date were the load-bearing choice, not the title. Dated
inside the return window, 2001 would have become a third option in the
"I'd like to return a book" clarifying menu, changing Scenario 2's reply and
re-blessing three more golden transcripts; dated as a recent order it would
have displaced a title in the order-history preview. Delivered-out-of-window
keeps it name-addressable — "2001", "Space" and "Odyssey" are all non-generic,
so it clears the write-path title guard without a `generic_words` collision —
and gives an honest out-of-window denial with the same reason code any other
old order gets, while touching **no demo envelope**: every idempotency key and
amount is byte-identical to v3.0.0, and only the two history counts moved. This
is the same move entry 37 made when it dated Exhalation *inside* the window on
purpose, run in reverse. Leaving `orders_placed` at 37 was not an option — it
would have reintroduced the exact record/store mismatch entry 37 exists to
close, one order larger. And because the count is pinned in code and prose, the
edit was deliberately a coordinated one: the profile, three assertions in
tests.py, the re-blessed transcript, README and DEMO.md all moved together, the
way entry 33 requires.

**Rejected.** Dating 2001 inside the return window or as a recent order (more of the demo
re-blesses for no gain); leaving `orders_placed` at 37; rewriting the historical
count citations in this file rather than adding this entry.

**Answers the question.** You added a book — did that change any decision the demo makes? Why is *that*
one not in the returnable menu?

**Lives in.** `profiles/bookly.json (orders BK-2132, catalog.titles, customer.orders_placed), transcripts/order-history-and-identity.json, tests.py (profile_load_preserves_the_fixtures, an_aggregate_question_is_not_answered_with_one_order), README.md, DEMO.md, issue #32`

---

## 43. The catalog ships hand-drawn cover art, through the seam the generator already left for it

*v3.1.0*

**Decision.** Every catalog book gets a bespoke, hand-drawn cover — a scene, a
concept, or an item distinctly associated with the book (Dune's sandworm, the
impossible teapot, the rubber duck, a Penrose triangle, the Ubik spray can) —
committed as a static `covers/<order_id>.svg` and served through
`covers.override_for`, which already won over the generated jacket by design.
The procedurally-generated jacket stays untouched as the fallback for anything
without drawn art. A new check, `override_covers_carry_no_forbidden_sink`,
holds every file in `covers/` to the *same* escaping guarantee the generator
has — no `<image>`/`href`/`<script>`/`url(`/`@import`, exactly one `http` (the
xmlns) — and asserts the signed-in customer's orders each resolve to their
override rather than the stand-in.

**Why.** The seam was built for exactly this a phase ago — "real art should always be
able to beat generated art" — so the honest way to add art was to use it, not
to teach the generator about specific books. The generator *can't* draw a scene
from a book: its whole input is a hash of title and author, which cannot know
Dune has a sandworm, so anything book-specific has to be authored. The
definition was deliberately loosened from "a scene" to "a scene, concept, or
item" because a third of the catalog has no single iconic scene — *The
Pragmatic Programmer* has no image, but rubber-duck debugging is unmistakably
its emblem — and a crude literal scene would read as generated, the one thing
this art exists not to do. Flat fills only, no gradients: an SVG gradient is
referenced as `fill="url(#id)"`, and `url(` is on the forbidden list the
injection claim rests on, so the whole catalog is shaded with layered flat
shapes instead. The new check exists because authored art is a second place a
markup sink could enter the build, and "the sink cannot exist" has to stay a
claim about structure rather than about who drew carefully — the same argument
entry 38 makes about the client. Adding overrides also forced a correction to
`covers_are_deterministic_and_need_no_network`: it compared `for_order` against
`render`, which was only ever equal because no order had an override; now that
every catalog order does, the check exercises `render` directly, which is what
it was always really testing. Adding the check moved the count 68 → 69, so
seven citations moved with it, the way entry 33 requires.

**Rejected.** SVG gradients (the `url(` ban); teaching the generator about specific books
(it has no book-specific input, by design); requiring a literal scene for every
title (some have no iconic one, and a forced one reads as generated); leaving
the determinism check comparing `for_order` to `render` once overrides made
that false; committing the scratchpad authoring tool into the repo — the static
SVGs are the source of truth, the way deck/ output is.

**Answers the question.** Aren't these just the plain generated jackets with nicer colours? How do you
know a hand-drawn cover can't smuggle in the markup sink you spent a phase
proving doesn't exist?

**Lives in.** `covers/ (39 files), covers.py (override_for, for_order, OVERRIDE_DIR), tests.py (override_covers_carry_no_forbidden_sink, covers_are_deterministic_and_need_no_network), issue #33`

---

## 44. The wordmark is drawn in neutral ink, because the brand colour is already spoken for

*v3.1.0*

**Decision.** Both surfaces get a hand-drawn open-book mark beside the wordmark — static
inline SVG in the topbar, the same mark on the console and the back office so
they read as one product. It is drawn in `currentColor`, inheriting the
wordmark's white, and is **never** purple. It is a sibling of the JS-filled
`#brand` span, not inside it, so `clear(dom.brand)` on boot leaves the mark
standing.

**Why.** The obvious thing to do with a logo is paint it the brand colour, and the
profile's `brand.accent` is `#5754FF` — which is exactly `--purple`. In this
build purple is not a brand colour, it is the deterministic side of the
provenance boundary, and entry 39 spends it on nothing decorative. A purple
mark would be the first decorative purple in the interface, quietly telling the
eye that the logo is "the deterministic side", which is meaningless and erodes
the one association the whole console teaches. So the rule wins over the
convention, exactly as the branch was briefed to resolve it: neutral ink, and
the mark carries no side. It is a sibling of `#brand` rather than a child
because the console fills the wordmark text from the profile at boot with
`clear(dom.brand)`, and a mark inside that node would be wiped on the first
render — the same "the interface names the speaker" plumbing entry 18 leaned
on. Drawn rather than fetched keeps the no-network, no-image-file, CSP-clean
constraints the rest of the build already holds: inline `<svg>` is neither
script nor style nor an external image, so it passes `script-src 'self';
style-src 'self'; img-src 'self' data:` untouched.

**Rejected.** Painting the mark the profile's brand accent (it is the provenance purple);
an image file or an icon-font glyph (a network fetch and a dependency); putting
the mark inside `#brand` (boot would clear it).

**Answers the question.** Why isn't your logo your brand colour? Where does the mark come from — is it
an image?

**Lives in.** `static/index.html, static/backoffice.html, static/app.css (.brand, .brand-mark, .brand-text), issue #34`

---

## 45. The polish pass was kept surgical, on purpose

*v3.1.0*

**Decision.** The "visual polish" item shipped as one change: the record-column jacket grew
from 44×66 to 48×72 with a small radius and a soft drop shadow, so the drawn
art reads as a cover rather than a favicon. Spacing, type and the empty states
were left as they were. No transition was added.

**Why.** The console was already a deliberately designed surface — its layout *is* the
boundary diagram (entry 39), its colours carry provenance, its motion is spent
in exactly one place. The cosmetics that made this read as a finished product
were the logo and the 39 covers; against that, restyling a mature interface is
mostly downside, because every spacing or colour move is a chance to weaken a
claim the design is making on purpose. The one change worth making was the one
the new art demanded: art shown at favicon size is art wasted, so the jacket
earned room and a lift. A hover transition on the order card was written and
then removed — motion is spent on the trace stream and nowhere else, and a
120ms fade on a record row would spend it a second time for decoration. The
restraint is the decision: a sub-version that changed less than it could have,
because the brief was to make it read finished without weakening a claim, and
the surest way to weaken one was to redesign around it.

**Rejected.** A broader restyle of spacing, type and density; a hover transition on the
order cards (spends the motion budget a second time); enlarging the jacket
without the shadow (reads flat against the card).

**Answers the question.** A whole sub-version for polish, and you moved one number? Why didn't you
touch the rest?

**Lives in.** `static/app.css (.order img, button.order), issue #35`

---

## 46. The policy is authorable now — the surface earlier builds refused to mock, built for real

*v3.2.0*

**Decision.** Reversal. Entries 29 and 41 shipped a *read-only* policy viewer and said, on
screen and in the record, that this build ships no editing surface because
"making procedures authorable by non-engineers is the next order of problem, and
mocking it would be the one dishonest thing on screen." v3.2.0 builds it — for
real, not a mock. The three CX thresholds (`RETURN_WINDOW_DAYS`,
`MAX_CLARIFY_ATTEMPTS`, `DENIALS_BEFORE_ESCALATION`) are authored from the back
office: `policy.py` reads them from an append-only, validated document instead
of from literals, a non-engineer edits them with an actor and a justification,
and the console reads the change live. Two things are deliberately **not**
authorable and stay code: the decision *structure* (the functions and reason
codes), and the two floors that stop a confidently wrong answer —
`MIN_TITLE_WORDS_FOR_WRITE` and `tools.MIN_KEYWORD_MATCHES`.

**Why.** The refusal was right *when it was made* — an editor built then would have been
a mock, and a mocked policy editor on the one screen whose job is honesty is
worse than an absent one. What changed is that it is real now, so the honest
move flips with it. The design is what makes the reversal safe rather than a
climb-down, and every piece answers a question a sceptic will ask. *Does this
break "policy.py is the only place a verdict is computed"?* No: the authored
document holds only numbers the module already understood, resolved through a
module `__getattr__`; a decision reads a threshold exactly as it always did, the
number just comes from a validated document — a change of storage, not of
authority, and the language model is still nowhere near it. *Can a non-engineer
break the agent?* No: every edit is range- and type-checked and the bounds hold
on read as well as write, so a value that would move money wrongly is refused —
which is the whole reason the two anti-"confident wrong answer" floors are the
exact things left in code. Lowering `MIN_TITLE_WORDS_FOR_WRITE` to 1 would
re-open the coincidental-word refund entry 12 closed; lowering the retrieval
floor would re-open the wrong-article answer entry 9 closed. The point of a
floor is that it does not get lowered, so it is not a dial. *Where is the
authored change, and who made it?* In an append-only log — the same shape and
ethos as the review queue in entry 28: an edit is a new event carrying who,
what, why and when, a revert is another event, nothing is overwritten, and the
mutation is a single validated POST rather than a REST update or a destructive
verb. The editor lives only on the operator surface; the customer console has no
route to author policy and is refused one by a check. And an un-edited build
decides on exactly the historical numbers — asserted — so shipping all of this
moved no envelope. This is also the honest answer to the standing Decagon
question (entry 41): the convergence with Agent Operating Procedures is real,
and the authorship gap it named is now half-closed — CX authors the parameters;
authoring new *rules* (a small procedure DSL) is the harder step still ahead,
and its being harder is exactly why it is not smuggled into this branch.

**Rejected.** Authoring rules — a procedure DSL — in this branch (the larger, riskier
problem; scoped out on purpose and named as the next step); making the floors
authorable, which would hand a non-engineer the dials entries 9 and 12 exist to
weld shut; a REST update or overwrite instead of an append (it would destroy the
record an auditor needs); putting the editor on the customer console; and
rewriting entries 29 and 41 rather than leaving them as the true account of what
was refused and why, with this entry as the reversal.

**Answers the question.** Isn't this the exact thing you said you couldn't do without mocking it? Can a
CX manager change the return window without an engineer — and can they break the
agent doing it? This looks like Decagon's AOPs — is it?

**Lives in.** `policy.py (PARAMETERS, active_policy, change_parameter, policy_changes, __getattr__), policy.json, web.py (policy_json, checks_command), backoffice.py (_policy_change), static/backoffice.js (policyEditor), tests.py (policy_defaults_are_the_historical_policy, an_authored_change_moves_a_verdict_through_policy_only, a_policy_change_is_validated_and_requires_an_actor, the_policy_log_is_append_only_and_reloads_live, a_hand_edited_document_cannot_push_a_threshold_out_of_range, policy_is_authored_in_the_back_office_and_the_console_reads_it), issue #37, supersedes entries 29 and 41`

---

## 47. The agent knows when it does not know — a door for "none of the above"

*v3.3.0*

**Decision.** `out_of_scope` is a real intent. The extraction prompt tells a hosted model to
use it for a request that fits none of the known intents — never forcing it
onto the nearest one, and never using it for a request the listed intents
already cover, so the known intents always win. The stand-in gets the same
door: a segment that matched no handled intent and still reads as a request or
a question (a trailing `?`, or a leading interrogative or request verb) is
`out_of_scope`; a pleasantry carries no such signal and still gets the friendly
opener. The agent handles `out_of_scope` by naming the limit and offering a
person, recorded on the deterministic side. One such turn is answered honestly;
a second in a row escalates to a human — the dispute pattern from entry 8
applied to scope — governed by a code constant, `UNHANDLED_BEFORE_ESCALATION`,
that is deliberately not authorable.

**Why.** This closes the class entry 14 left open. The failure there was not that the
agent was wrong — it was that it could not tell it was out of scope, and a
hosted model with no "none of the above" to report mapped an unmodeled question
onto the nearest order-shaped intent and answered it confidently. A door is
what the class needs, and entry 14 said so: "a door only helps if the model
knows it is there." The subtlety is that entry 14 also *rejected* "a catch-all
fallback intent" — and this must be reconciled rather than waved past, because a
sceptic will notice. What entry 14 rejected was using a catch-all to *avoid
modelling answerable questions*: "how many books have I ordered" is answerable,
so it earned a real intent (`order_history`), not a shrug. That still holds, and
the answerable intents are all still there. `out_of_scope` is the opposite
move — the door for genuinely *unmodelled* topics — and it carries the inverse
risk of force-fitting: a model that dumps answerable questions into the door to
avoid thinking. That risk is bounded structurally rather than hoped away. The
prompt scopes the door to requests no listed intent covers and states the listed
intents win; the stand-in checks it last, only after every handled intent has
failed; and a check asserts that every answerable question — status, history,
identity, refund status, policy, return, handoff — still routes to its own
intent and never to `out_of_scope`, so the door provably swallows nothing the
agent can do. The escalate-on-persistence is what finally makes real the failure
mode this build has advertised since phase 1 and never fired: anything uncovered
reaches a human. Auto-escalating every stray question would flood the queue and
be wrong — "do you sell e-readers?" is not a case — so a single out-of-scope
turn is a decline and an offer, and only a *repeat* is a customer the agent
cannot help, exactly as a *repeated* denial becomes a dispute. The boundary the
whole repo rests on is unmoved: `out_of_scope` is extraction — the model or the
stand-in reporting that it recognised nothing — and the decline and the
escalation are computed by ordinary code. The model saying "I don't know" is
still the model conversing, not deciding.

**Rejected.** A lower confidence threshold, or a catch-all that fires on uncertainty rather
than on genuine absence of a home — a fallback triggered by low confidence
cannot tell "I can't answer this" from "I should answer this with intent X but
I'm unsure", which is the distinction that matters; enumerating out-of-scope
topics one at a time, the very thing entry 14 said does not fix the class;
auto-escalating every out-of-scope turn (a case per stray question); and making
`UNHANDLED_BEFORE_ESCALATION` authorable in this branch — it governs when a
conversation reaches a human, and that stayed in code on purpose.

**Answers the question.** What does it do when a customer asks something you didn't model? Didn't you
reject a catch-all fallback in entry 14 — isn't this that? You've said uncovered
questions escalate since phase 1 — does that actually fire now?

**Lives in.** `llm.py (VALID_INTENTS, OUT_OF_SCOPE_RE, _intent_of, _out_of_scope, EXTRACTION_SYSTEM_PROMPT, _escalation), agent.py (_is_actionable, _answer_out_of_scope, unhandled_streak, handle_turn), policy.py (UNHANDLED_BEFORE_ESCALATION, unhandled_limit_reached, ESCALATED_UNHANDLED, REASON_CODES), transcripts/out-of-scope-then-escalation.json, tests.py (an_out_of_scope_request_is_recognized_not_force_fit, persistent_out_of_scope_escalates_and_a_handled_turn_resets), issue #39, extends entries 8 and 14`

## 48. The orchestration layer is real now — retries, dead-letter, durable dedup

*v3.4.0*

**Decision.** Reversal. Entries 4, 29 and 41 shipped the receiving end as a deliberate
stub — "nothing in the repo retries", "deduplication is in memory and dies with
this process", "durable dedup is the real receiver's job" — because claiming
durability the demo did not have, on the one screen whose whole job is the
exactly-once claim, would have been the dishonest thing. v3.4.0 builds it. A
failed hop no longer records `failed_unreachable` and moves on: the envelope is
appended to a **durable outbox**, and `reconcile()` re-delivers it with bounded
exponential backoff, moving one that exhausts `MAX_DELIVERY_ATTEMPTS` to a
**dead-letter** store for a human. The back-office ledger **persists and reloads
on start**, so it dedups across a restart. Reconcile is a watched action — a CLI
and a console button — not an always-running worker.

**Why.** The whole point of the envelope contract from entry 4 was that it "already
accommodates the retries, dead-lettering and durable idempotency store this repo
deliberately does not implement." So building them changed no contract: the
envelope bytes and the idempotency key are byte-identical, which is exactly what
makes a retry safe — the same decision re-delivered hashes to the same key, and
the receiver suppresses it. Every piece answers a question a sceptic asks.
*What happens if the downstream system is down — is the refund lost?* No: the
decision is audited before the hop as it always was, and now the envelope waits
in the outbox instead of vanishing, so reconcile finishes the delivery when the
receiver returns. *Is it double-posted when it comes back?* No: the ledger's
dedup is durable, so a re-delivery it already recorded — the classic lost-ack
case — is suppressed rather than executed twice, exactly once across the
failure, and a check drives the whole loop end to end to prove it. *Doesn't
retrying put the executor back inside the decision loop?* No: this is all on the
executor's side of the boundary. The agent still emits, the outbox enqueue is
transparent to it, the delivery string is still never branched on, and reconcile
is a separate operation — so the agent's turn never blocks on a dead receiver
(one fast attempt, then the outbox). No decision logic moved outside policy.py;
retries and dedup are execution, not verdicts. The honesty move from entry 29 is
kept by inverting it: the on-screen "in memory; dies with this process" notice
becomes "durable", because it now is — the claim is real rather than mocked. And
`stub_receiver.py` stays the in-memory drop-in, so "run one or the other" holds
and its own smaller honesty is untouched — the real receiver's job is the back
office's, now done.

**Rejected.** An always-running background worker draining the outbox on a timer — less
watchable and more concurrency to reason about, so reconcile is a manual action
you run and see; sqlite for the durable stores — JSON keeps them consistent with
the queue and the policy document and readable in a terminal during the demo;
synchronous in-turn retries — they would block a turn while a dead receiver
timed out, which is the opposite of "emit, don't execute"; making
`MAX_DELIVERY_ATTEMPTS` authorable — it is an execution knob, and it stayed in
code; and touching `stub_receiver.py`, which stays simple on purpose.

**Answers the question.** What happens if the downstream system is down — does the refund get lost, or
double-posted when it comes back? Is the deduplication durable now? Didn't you
say nothing in the repo retries?

**Lives in.** `envelope.py (outbox, reconcile, _enqueue_outbox, _append_deadletter, MAX_DELIVERY_ATTEMPTS, dead_letters), reconcile.py, backoffice.py (Ledger persistence, ledger_path), web.py (outbox_json, reconcile_json, /api/reconcile, /api/outbox), static/app.js (refreshOutbox), tests.py (a_failed_delivery_waits_in_the_outbox_rather_than_vanishing, reconcile_backs_off_and_dead_letters_after_its_attempts, the_ledger_dedups_durably_across_a_restart, a_reconciled_delivery_posts_exactly_once_across_a_failure), issue #41, supersedes the durability limits in entries 29 and 41`

## 49. The full code review — what was consolidated, and what duplication is kept on purpose

*v3.5.0*

**Decision.** A review pass over the whole build, four readers in parallel — decision core,
execution boundary, web and UI, test harness. The verdict was that the build is
clean and the real debt was duplication introduced across v3.2–3.4, so that is
what the pass removed. The console's UI helpers (`api`, `money`, `day`, `fact`,
`emptyState`, and the parameterised `resolveForm`) are now exported from the
console client and imported by the back office, extending the pattern entry 38
already blessed for `el`/`clear` — one place a value is formatted, one place a
markup sink could appear. The suite's isolation folded into three helpers
(`_temp_env_paths`, `_webhook`, `_recording_provider`). `emit`/`emit_resolution`
share one `_dispatch` tail; the six runtime-path env vars became one `DATA_PATHS`
table; and a handful of clear no-downside fixes landed (a stale intent comment,
a dead `factOf` line, an unused import, a garbled docstring, a redundant outbox
read). **One duplication was deliberately kept and documented rather than
removed:** the atomic JSON-store primitive (`tmp.write → tmp.replace`) and
`_utc_now` appear in `envelope.py`, `queue.py`, `backoffice.py` and `policy.py`.

**Why.** Consolidating that last one would mean a shared `utils`/`jsonstore` module, and
this repo has avoided a shared grab-bag on purpose: each module stands alone and
imports only what it genuinely depends on, so "what does this file touch" is
answerable by reading the file. A four-line atomic write repeated in four places
is the price of that independence, and it is a price worth paying here — the
primitive is stable, stdlib-only, and unlikely to change, so the copies will not
drift into disagreement the way the UI helpers or the test isolation were
starting to. That is the distinction the pass drew throughout: duplication that
was *accumulating and diverging* (five hand-rolled webhook save/restores, two
copies of a date formatter about to drift, a decision-path tuple one check could
update and another miss) is real debt and was removed; duplication that is
*small, stable, and the cost of a deliberate structure* is not debt and was left,
with this entry as the record so a future reader does not "helpfully" unify it.
A few other things were checked and left for stated reasons: the back office's
longer key tail (its cards are not space-constrained, so `shortKey` genuinely
differs), `stub_receiver.py`'s repeated `server_bind` (entry 48 keeps it
simple), the six overlapping decision-field tuples (each pins a different subset
on purpose), and the hosted-provider `api_key` ternary (explicit on the
never-exported-key path, entry 24). No behaviour changed and no check was
weakened: every consolidation kept the suite green at 82 and the envelopes
byte-identical, verified for the JS by rendering both surfaces since the Python
suite does not exercise them.

**Rejected.** A shared utils module for the JSON/time primitives (the module-independence
stance is deliberate and worth more than four saved lines); unifying `shortKey`
(the length difference is real); sharing `caseHistory` (it closes over
`shortKey`, so sharing it would change the back office's key length as a side
effect); and collapsing the decision-field tuples (each asserts a different
subset, so one constant would over- or under-assert somewhere).

**Answers the question.** Isn't the same atomic-write helper copied in four files — is that not exactly
the technical debt this pass was for? Why keep some duplication and remove other
duplication?

**Lives in.** `static/app.js (exported helpers, resolveForm), static/backoffice.js (imports), envelope.py (_dispatch), web.py (DATA_PATHS, DELIVERY_STATES), tests.py (_temp_env_paths, _webhook, _recording_provider, DECISION_PATH_MODULES), issue #43`

## 50. v4.0.0 is cut — the same architecture, refined until it reads as a product

*v4.0.0*

**Decision.** v4.0.0 is the release, cut once every item on the version-3 plan had landed:
cosmetics (v3.1.0), authorable policy parameters (v3.2.0), the agent knowing
when it does not know (v3.3.0), the orchestration layer becoming real (v3.4.0),
and a full code review (v3.5.0). No code changes with this tag — it is the
marker for the finished build, not a new feature. The version-3 architecture is
unchanged: the language model still never decides, `policy.py` is still the only
place a verdict is computed, and the demo envelopes are byte-identical to
v3.0.0, asserted at every step across all five sub-versions.

**Why.** A major version says "this is the shape it was meant to have", and that is
what the arc produced rather than any single commit. Two things about how it got
here are worth keeping. First, the sub-versions were not additive features
bolted on — three of them *reversed* a limit v3.0.0 had shipped **as a
deliberate refusal to mock**: the read-only policy viewer became a real,
validated, append-only editor (entry 46); the in-memory ledger became durable,
with retries and dead-letters and exactly-once across a failure (entry 48); and
the unmodelled-question class the intent surface left open got its door (entry
47). "Refuse to mock it, then build it for real when it can be real" is the
through-line, and it is why the reversals read as the plan working rather than
the plan changing. Second, every reversal was recorded against the entry it
overturned rather than editing history, so this file still contains the honest
account of what was stubbed and why, next to what replaced it — which is the
same discipline the whole repo rests on. What stays out is stated, not hidden:
embeddings for retrieval remain parked behind the no-dependencies constraint,
and the review (entry 49) kept some duplication on purpose. There is no
CHANGELOG, on purpose: the git tags, this file, the commit messages, and the
closed issues already are the record, and a separate copy would be exactly the
duplicated-fact problem the count-citation check (entry 33) exists to prevent.

**Rejected.** Cutting v4.0.0 before the plan finished (the gate was explicit — all of them
land first); a CHANGELOG.md duplicating what the tags, issues, and this file
already carry; and treating a major bump as a licence to change the
architecture, when the point is that it did not have to.

**Answers the question.** What does v4.0.0 add over v3.0.0 if no decision changed? Why a major version
for a release that ships no new verdict?

**Lives in.** `CLAUDE.md (version and plan), the v3.1.0–v3.5.0 tags and their entries (42–49), issue #45`

## 51. Cutting the release needed a full prose sweep, not the per-branch edits

*v4.0.0*

**Decision.** Before the v4.0.0 tag was allowed to stand, the whole project was
evaluated for alignment — code, README, READING_GUIDE, DEMO, deck, wiki,
evidence, and the issues. The code was clean and green; the narrative artifacts
were not. They had drifted, all in one direction: the prose still described as
*future* several capabilities the v3.1→v3.5 arc had since built. The fix was a
single alignment sweep across every document, plus two artifact corrections that
were not documentation at all: the back office's third surface still read
**Policy viewer** when v3.2.0 had made it an editor (now **Policy editor**), and
the stand-in evidence (`demo_transcript.txt`, `audit_trail.txt`) was re-captured
against v4.0.0 while the hosted evidence kept its dated provenance note rather
than being re-billed (entry 40). DEMO.md gained the three beats the arc earned —
authoring a threshold to flip a real verdict, declining then escalating an
out-of-scope turn, and reconciling a failed delivery until it posts exactly
once — and grew from twelve minutes to fifteen.

**Why.** On a multi-sub-version arc, per-branch doc edits are targeted at the
branch, so each one leaves the surrounding prose describing the *old* world. The
drift is therefore systematic and always the same shape — "future tense" left
standing over shipped work — and the only reliable catch is a full sweep at the
release cut. The one part that did not drift was the check count, because
`documents_state_the_actual_check_count` (entry 33) fails the suite when a number
goes stale. Nothing does that for a prose claim that a capability is unbuilt,
which is exactly why that class of drift survived to the release gate. The most
telling instance: the evidence NOTEs on the hosted transcripts had gone
*backwards* — written the same day the persona was added, they promised "run it
today and you get 'I'm sorry Dave'", but the persona was struck hours later
(entry 18), so by the release the plain phrasing they showed matched the live
agent again and the note contradicted its own file.

**Rejected.** Tagging on green checks alone (the checks never saw the prose
drift, by construction); re-billing the hosted-provider runs to refresh their
transcripts, when a dated provenance note is the standing answer (entry 40);
and adding a doc-linter check for "future tense" claims, which would guard a few
phrasings and miss the class — the sweep-at-release discipline is the real
control, recorded here rather than automated.

**Answers the question.** The build is green and the architecture is sound — why
does cutting the release still take a full read of every document? Because green
checks prove the code, and nothing but a human read proves the story the
documents tell about it is still true.

**Lives in.** `every narrative doc (README, READING_GUIDE, DEMO, docs/wiki/Home), static/backoffice.html + backoffice.py (Policy editor), evidence/, issue #47`

## 52. The hosted path had four defects a live demo found and the stand-in never could

*v4.0.1*

**Decision.** A live demo on the OpenAI provider surfaced four distinct
defects, none of them visible to the rules-based stand-in and none of them
caught by 82 offline checks. All four are fixed here, each at its actual
root rather than at the symptom the demo happened to show:

- **`GENERIC_TITLE_WORDS` was missing the definite article** (issue #51).
  Twelve of the catalog's thirty-nine titles contain "the", so it scored as
  distinctive purely by absence from the list, and naming a book by its
  title lost to the word "the" appearing in ten titles nobody named. Fixed
  as catalog data — `a`, `an`, `and`, `for`, `in`, `of`, `the`, `to` joined
  the same list `book` and `copy` already sit in — not as a change to
  `title_reference_is_strong`, whose threshold was never wrong. The same bug
  was the whole explanation for a negated answer snapping onto the wrong
  offered option (issue #52): with the clarify budget exhausted by false
  matches on "the", there was nothing left to bind to but a guess.
- **The extraction brief described what `refund_status` was for and never
  what told it apart from a renewed demand** (issue #53). "I don't care what
  the policy says, refund it anyway" — spoken immediately after a denial —
  read as a question about a refund that already existed, so the turn never
  reached `policy.py` at all: no verdict, no envelope,
  `escalate_if_disputed` never ran. Fixed in the prompt, with the failing
  phrase named as an example and the actual test stated in words: money
  that already moved, or money being demanded.
- **A repeat escalation onto an already-open case kept its reason in the
  data the whole time; neither UI rendered it** (issue #50). `queue.py`'s
  append-only design was never the bug — a manager request pushed onto a
  policy-dispute case correctly appended `ESCALATED_CUSTOMER_REQUEST` to
  `events`, every time. Fixed in `static/app.js` and `static/backoffice.js`
  independently, each still rendering the reason on every event that carries
  one — not merged into one function, because the two files deliberately
  keep different `shortKey` widths and a shared implementation would have
  quietly taken that decision away from one of them.
- **Narration was returned with nothing checking it against the event that
  produced it** (issue #49). Extraction has validated untrusted model output
  field by field since entry 7; narration never did. Handed a correctly
  computed `{"refund": null}`, the hosted model said "your refund has been
  approved" — confirmed, later, not to be a fluke: called on that exact
  event in isolation, five separate times, it made the same false claim
  five times (entry 53). `_narration_is_grounded()` is narrow on purpose —
  not "does the prose match the decision" in general, only "does the text
  claim a refund the facts do not grant" — and keyed on the facts rather
  than the kind, because a genuine refund-status answer about a refund
  already issued legitimately says "your refund is on its way," and a
  kind-only check would have rejected that correct sentence along with the
  false one. On a mismatch it falls back to `_TEMPLATES[event.kind](event.facts)`
  — the same sentence the stand-in would already say, built from the same
  facts — so the fix adds no new prose to maintain.

**Why.** Three of the four are the same shape: untrusted model output on the
hosted path, checked nowhere, in three different places — a data list a
title could be measured against, a prompt deciding which branch a demand
routed through, and a sentence describing a decision after the decision was
already made. The fourth (#50) is a different shape entirely — the
deterministic side was already correct and already durable, and the only
defect was that a correct fact sat in `events` unread by two UI files —
which is worth keeping distinct precisely because it proves the boundary
held even where the UI did not: the case never lost data, it only failed to
show it.

None of the four defects reproduces on the rules-based stand-in, which is
why 82 offline checks — the whole suite, at the time — never caught them.
`#51` and `#53` are language a hosted model reads and a regex does not
paraphrase; `#49` is a hosted model's own free text, which the stand-in's
template function cannot produce a wrong version of by construction; `#50`
reproduces on any provider, but the demo's own scripted scenarios never push
a second, different escalation reason onto an already-open case, so nothing
exercised the render path this build actually ships. This is the argument
for the live re-run discipline recorded in entry 53, not just for these four
fixes.

**Rejected.** For #51/#52: touching `policy.title_reference_is_strong` or its
`MIN_TITLE_WORDS_FOR_WRITE` threshold — the disambiguation rule was correct
throughout, and moving it would have been fixing the wrong module for a bug
that was entirely catalog data. For #53: teaching `_answer_refund_status` to
also check policy on a null-refund path — that would duplicate the decision
`_handle_return` already makes correctly, and duplicated decision logic is
exactly what this repo's whole boundary argument exists to prevent; the fix
belongs where the misclassification happens, once. For #50: a shared
`caseHistory` helper between the console and the back office — it would have
silently regressed the back office's longer `shortKey`, a difference the
code already comments as deliberate; two small, separately-verified fixes
cost less than one shared function with a side effect neither file asked
for. For #49: validating the whole sentence against every fact in the event
— a general "does the prose match the decision" check is a second
decision-maker in miniature, contradicting the boundary it exists to
enforce; the narrower "does it claim a grant the facts do not carry" is
answerable without judging the sentence's other content at all.

**Answers the question.** If the demo never fails locally, how do you find
what only breaks on a hosted model? You run the hosted model, on the actual
words a customer said, and read the transcript rather than the checks.
Three of these four defects have no offline test that would ever have
caught them before this incident; all three do now.

**Lives in.** `profiles/bookly.json (catalog.generic_words), llm.py
(EXTRACTION_SYSTEM_PROMPT, _narration_is_grounded, _claims_a_refund_was_granted,
HostedProvider.narrate), static/app.js + static/backoffice.js (caseHistory,
distinctPushReasons), queue.py (unchanged — the reference point proving the
data was already correct), tests.py
(an_article_inside_a_title_does_not_drown_the_real_word,
disputing_a_denial_is_a_return_request_not_a_refund_question,
a_manager_request_on_an_open_case_keeps_both_reasons,
a_refund_claim_the_facts_do_not_grant_falls_back_to_the_template,
a_genuine_refund_narration_passes_through_unreplaced), issues #49–#53, PR #54`

## 53. Confirming a fix needs the live model again, not just the absence of the original repro

*v4.0.1*

**Decision.** After #51 and #53 landed, the original eight-turn transcript
was replayed against the live OpenAI provider and came back clean — no
hallucinated refund, no mis-bound title, no misrouted dispute. That was not
treated as confirmation that #49's narration-grounding gap was closed, or
even that it still mattered. It was one route through the model no longer
producing the failure, which is a different claim from the model no longer
producing it. To tell the two apart, `HostedProvider.narrate()` was called
directly — outside the eight-turn script, outside `agent.py` entirely — on
the exact event the model had actually been handed during the incident
(`kind="refund_status"`, `facts={"refund": None, ...}`), five times in a
row, against the real model. It claimed an approved refund on all five
calls.

**Why.** #53's fix closed the *route* that used to feed the model this
event — "refund it anyway" now reaches policy and never falls through to
`_answer_refund_status` with a null refund in the first place. It changed
nothing about what the model does when it is actually handed that event,
because it could not have: extraction and narration are different jobs,
called at different points, and fixing one call site's inputs does not
touch the other's behaviour. An end-to-end re-run that came back clean was
consistent with either story — the model getting more careful, or the demo
simply not asking it the dangerous question anymore — and only the second
story turned out to be true. Isolating the call proved it: same event, same
model, hallucinated every time, with or without the routing bug. The guard
added in entry 52 is not a defensive extra for a problem that went away; it
is the only thing currently stopping a five-for-five failure from reaching
a customer.

**Rejected.** Treating the clean eight-turn re-run as sufficient evidence to
close #49 — it was reported as a live re-run and taken as encouraging once
before the isolated call was made, and recording the correction is the
point of this entry. Re-running the full eight-turn script repeatedly until
a hallucination happened to reproduce end-to-end — slower, and the result
would still only ever show that the routing bug was gone, not that the
narration bug was; the routing bug being gone was never in question.

**Answers the question.** You already re-ran the demo and it worked — why
did #49 need its own verification? Because the demo working end to end and
the narrator being trustworthy are two different claims, and a fix to one
route proves nothing about the other unless you go and ask it directly.

**Lives in.** `llm.py (_narration_is_grounded)`, issue #49 (closing comment,
with the full 5-for-5 transcript), PR #54

## 54. v4.0.1 is cut — a bug-fix release, the architecture unchanged

*v4.0.1*

**Decision.** Four defects (entry 52), all on the hosted path, all
confirmed against the live provider rather than asserted from the fix.
`main` moves from `v4.0.0` to `v4.0.1` — a patch, not a minor or major
version, because nothing about the architecture, the envelope shape, the
policy thresholds, or the decision structure changed. Every envelope field
in the demo scenarios is byte-identical to v4.0.0. `policy.py` still
imports no LLM.

**Why.** Semantic versioning reserves a patch number for exactly this: a
fix that changes no contract. `GENERIC_TITLE_WORDS` gained entries; it is
still catalog data, read the same way. `EXTRACTION_SYSTEM_PROMPT` gained a
paragraph; it still produces the same `Request` shape, validated the same
way on the way in. `_narration_is_grounded` is a new function; it changes
which string `narrate()` returns on a narrow, specific mismatch and nothing
about what any caller of `narrate()` does with the result. None of the four
defects touched `policy.py`, and none of them could have without stopping
being a hosted-path defect and becoming a decision-layer one, which this
repo's whole boundary exists to make impossible.

**Rejected.** A minor version (`v4.1.0`) — nothing here is new capability,
and the version-3 arc's minor-version convention (entries 42–49) was
reserved for capability the previous release deliberately did not have;
these four were never deliberate. Bundling the fixes into whatever capability
work lands next rather than shipping them alone — the incident that found
them was a live demo, and the release-readiness discipline from entry 51
says a defect found in front of a customer-facing audience ships as its own
release, not folded into whatever lands next.

**Answers the question.** Why a new tag for four bug fixes instead of just
merging them? Because "unchanged since the last tag" is a claim worth being
able to make about every other file in the repo, and that claim needs a tag
of its own to point at.

**Lives in.** `CLAUDE.md (version line)`, the `v4.0.1` tag, PR #54, issues
#49–#53

## 55. "Exactly once" was the wrong word for delivery, and the code already knew it

**Decision.** `envelope.py`'s `reconcile()` docstring already stated this
correctly: "Exactly-once is not this function's job — it may re-deliver an
envelope the receiver already recorded — but the receiver dedups on the
idempotency key, so a duplicate is suppressed rather than executed twice."
That is at-least-once delivery with an idempotent consumer, which is the
accurate name for what this system does. It is not exactly-once delivery,
which this system does not attempt and does not need. The code was right.
Three places in the surrounding prose overclaimed anyway, promoting the
stronger-sounding term where the weaker, correct one was already sitting in
the same file: `backoffice.py`'s `Ledger` docstring called it "the
exactly-once contract," `docs/DECISIONS.md` entry 46 called it "the same
exactly-once promise the envelope already made," and entry 51 described
reconciliation itself as something that reconciles "a failed delivery to
exactly-once" — as if delivery were the thing becoming exactly-once, rather
than posting.

A full repo grep for "exactly-once" / "exactly once" found eighteen hits.
Fifteen describe the observable outcome — the refund posts once, the ledger
line is written once, a repeated push lands as one case — and that
framing is accurate: it is what durable dedup on the idempotency key
actually guarantees, and it is what a check drives end to end
(`a_reconciled_delivery_posts_exactly_once_across_a_failure`). Those fifteen,
including that test's own name, are unchanged. Only the three that named
delivery itself — a contract, a promise, a thing reconciliation produces —
were reworded, each to name what is actually true: delivery may repeat,
durable dedup on the key is what makes the posting land once regardless.

**Why.** Distributed-systems vocabulary is precise enough to be wrong in a
specific, checkable way. "Exactly-once delivery" is a real term of art for a
guarantee this system does not make and was never designed to make — the
outbox retries, the receiver may see the same envelope more than once, and
nothing here prevents that. What this system guarantees is idempotent
*consumption*: however many times the same decision is delivered, it is
posted once, because the key is derived from the decision itself and the
receiver's ledger persists and dedups on it durably, across a restart. That
distinction is not pedantry reserved for a systems-design interview — it is
exactly the question a technically literate skeptic asks first, and an
answer that says "exactly-once" without qualification does not survive the
follow-up. The fix costs nothing structurally: the accurate framing was
already written, once, correctly, in the one docstring whose whole job is
to say what `reconcile()` actually promises. Everywhere else just needed to
agree with it.

**Rejected.** Rewriting all eighteen hits uniformly to "effectively once" or
similar — most of them are already correct, and editing accurate prose to
avoid re-deriving the classification would have hidden the one real
distinction this entry exists to draw, between what the demo claims about
posting and what it does not claim about delivery. Renaming
`a_reconciled_delivery_posts_exactly_once_across_a_failure` or its docstring
— the test's own name is posting language, correctly used, and a test name
is not prose to be tightened; renaming it for no behavioral reason is a
diff with no reader benefit. Touching the captured evidence file
(`evidence/audit_trail.txt`) — its "exactly once across a failure" line is
also posting language, correctly used, and it is a captured transcript with
its own provenance discipline (entry 40); prose above a captured run gets
corrected for accuracy, not tightened for its own sake.

**Answers the question.** You cannot have exactly-once delivery in a system
with retries and an at-least-once outbox — so which is it? Delivery is
at-least-once. Consumption is idempotent. The refund posts exactly once
because of the second property, never the first, and every surface in this
repo now says so the same way `reconcile()` always did.

**Lives in.** `envelope.py (reconcile docstring, the canonical wording)`,
`backoffice.py (Ledger docstring)`, `docs/DECISIONS.md (entries 46 and 51)`

## 56. A second deck, for a different audience, not a replacement

**Decision.** `deck/build.js` still generates the five-slide technical deck
— depth, tradeoffs, what broke in the original build, argued to an engineer
who will check the claims. `deck/build-lean.js` is new: a four-slide deck
for a customer-facing audience, built to spend words on value rather than
mechanism. Same thesis, same boundary diagram, same palette — purple still
means only the deterministic side, never decoration — reached in four
slides instead of five, and a different fourth act: not "what I'd do next
architecturally" but "what's already production-shaped, and what a pilot
needs first."

The palette constants are a deliberate, exact duplicate between the two
files rather than a shared module. `build.js` already ships a tested,
working artifact; making `build-lean.js` depend on it — or refactoring both
onto a shared constants file — is a change to `build.js`'s own risk surface
for a benefit (twenty duplicated lines saved) smaller than the cost (one
build target's edit can now silently move the other one's slides). Two
small, independently correct files beat one shared one that makes both
decks a single point of failure.

The third slide of the lean deck tells the v4.0.1 story (entries 52-54) at
value level rather than mechanism level: a live demo found a real gap, zero
refunds ever moved, the guard that closes it was confirmed against the live
model five times. That fact was available before this deck existed, and it
is stronger material for a customer-facing slide than either of the
original build's two bugs — it is current, it is about the same hosted path
a customer would actually run, and it demonstrates the operating discipline
(catch it live, fix the root, verify against the real model, ship it) rather
than only the architecture.

**Why.** A deck arguing "prove you can trust an agent with money" to an
engineer and a deck arguing the same claim to a business audience are not
the same deck with slides removed — the engineer wants the tradeoffs named
and the failure modes catalogued; the business audience wants the claim,
one concrete moment that proves it, and an honest scoping conversation. Five
slides of the former shown to the latter reads as more technical depth than
the room asked for, which is the opposite of "dig out of the technical
deliverable." Forcing one build script to serve both audiences with a flag
or a config would have coupled two decks that should be free to diverge —
the customer deck should be free to change its emphasis without a technical
reviewer noticing a slide moved out from under them, and vice versa.

**Rejected.** Replacing `build.js` outright — the five-slide deck is tied to
a real, tagged release and its own DECISIONS entries; deleting it discards
that provenance for an audience it was never written for. Leaving the lean
deck as an artifact built outside the repo — a reviewer cloning the repo
and finding a deck that does not match the one they were sent is exactly
the kind of inconsistency this repo has otherwise been rigorous about
avoiding, and the whole point of `docs/DECISIONS.md` is that nothing
load-bearing lives outside it. A single parameterized build script
generating both — more code, one shared risk surface, and a flag
(`--lean`) standing in for what is actually two different arguments to two
different audiences, which deserve to read as two files, not one file with
a branch in it.

**Answers the question.** Why do the deck and the demo not match what's
being presented? They will now — the deck a customer-facing reviewer is
sent matches a deck this repo can regenerate byte-for-byte from source, the
same discipline `build.js` already held itself to.

**Lives in.** `deck/build-lean.js`, `deck/package.json (build:lean)`,
`deck/README.md`, `Bookly_Customer_Deck.pptx`

## 57. The Reset button, and the class of bug the checks could not see

**Decision.** Three fixes, one root discipline. `Api.reset` passes `{}` so
the call is a POST, matching `/api/reconcile`, which already did this and
already knew why. The Reset click handler gained the `try`/`catch`/`notify`
every other async handler in the build already had, and clears local state
only after the server confirms. And the request body is now taken off the
socket once in `web.py`'s `_route`, before dispatch, rather than by each
handler choosing to read it.

The three are recorded as one entry because they are one incident: a button
that did nothing, the silence that made it hard to diagnose, and the framing
bug that the first fix exposed.

**Why.** `api()` derives its verb from its arguments — a body means POST, no
body means the `fetch` default, GET. `/api/reset` was declared with no body,
so the button issued `GET /api/reset` at a route registered POST-only, took a
404, and abandoned the rest of the handler. It was the only mutating endpoint
in `Api` called without a body.

Fixing the verb then exposed something older. This console speaks HTTP/1.1, so
a browser sends the next request down the same socket. A handler that never
reads its request body leaves those bytes there; the server parses them as the
next request line and answers 501 to a request that was fine. **The symptom
lands one call after the cause** — Reset visibly worked, and then
`/api/provider` returned an HTML error page, reported as "returned something
that was not JSON." Only four handlers read a body. `/api/reset` never did,
and neither did `/api/reconcile`, which has been shipping that way since
v3.4.0 and was simply never clicked twice on one connection.

The fix is not to teach two handlers to read a body they do not want; that
leaves the trap armed for the next endpoint. Draining in `_route` covers the
404 and 421 paths too, where there is no handler to be disciplined.

**The correction worth recording.** The reason none of this was caught is not
that nobody wrote a check — it is that every check drove the API with
`urllib`, which opens a fresh connection per call and closes it. The stale
bytes went out with the socket. `curl` behaves identically, which is why the
first reproduction of the reset bug looked clean at the HTTP level while a
browser broke immediately. **The bug existed only under connection reuse, and
nothing in the suite reused a connection.** That is a statement about the
shape of the test harness, not about anyone's diligence, and it is the part of
this incident worth remembering: a check that cannot enter the state where a
defect lives will pass forever and prove nothing.

Each fix was verified by reverting it and watching its check go red, rather
than by observing green after the change.

**Rejected.** Registering `/api/reset` under GET as well — it mutates state,
rotates the audit log, and a GET that changes the world is the thing every
other route here avoids. Teaching `/api/reset` and `/api/reconcile` to call
`_body()` individually — correct for those two, silent for the third one
somebody adds. Sharing the console's handler with the back office's so the
drain covers both — the back office was checked and is already clean, and the
two handlers are deliberately separate for the same reason `static/app.js` and
`static/backoffice.js` do not share a `caseHistory` (entry 52). Bundling the
error surfacing into the first fix — the fix is one line and the surfacing is
a behaviour change to the failure path; they are separate decisions and the
history reads better with them apart.

**Answers the question.** "Your suite is green, so how did a dead button ship?"
Because green means every check passed, not that the checks covered the state
the defect lives in. Three checks now exist that did not: one asserting the
client's verbs match the server's routes, one asserting every async handler
surfaces its failure, and one that deliberately reuses a single connection
across two requests because that is the only condition under which the
framing bug is observable.

**Lives in.** `static/app.js` (`Api.reset`, the Reset handler), `web.py`
(`_route`, `_read_body`, `_body`), and the checks
`client_api_calls_use_a_method_the_server_routes`,
`every_async_handler_surfaces_its_failure`,
`a_post_body_never_desyncs_a_reused_connection`. Issues #57 and #60, PRs #58,
#59 and #61.

---

## 58. The demo clock is data, and moving it means moving the dataset with it

**Decision.** The frozen clock moved from `2026-08-17` to `2026-09-03` for a
second run of the demo, and every date in the profile that takes part in
relative reasoning moved forward by the same seventeen days: `today`,
`ordered_on`, `delivered_on`, `eta`, `returned_on` and the contact history.
Eighty-two dates in all. `member_since` stayed at 2021.

**Why.** This is the second time the clock has moved (entry 36 made it profile
data, issue #70 moved it the first time), which makes it a recurring operation
rather than a one-off, and worth writing down as a procedure.

A clock is meaningless on its own. Every claim the demo makes is a *distance*:
Godel, Escher, Bach was delivered twelve days ago and so refunds; Dune arrives
in two days and so is still in flight; the Pragmatic Programmer is eighty-nine
days out and so is denied. Moving `today` while the orders stay put does not
advance the demo, it deforms it — the refund loses its margin, and the order
still "arriving" arrives in the past. Shifting the whole dataset by one delta
preserves every distance exactly, which is why no scenario changed behaviour
and no reason code moved.

The clock is set to the first of the two presentation days rather than to a
midpoint. Nothing in the decision path reads the wall clock, verified in #70
by running the return flow with the system date faked to 2027-03-01 and
getting the same `$22.50`, so the second day runs the same turns with the same
output. There is no window this expires at the end of.

**Rejected.** Unfreezing the clock so it tracks the real date — every golden
transcript would then be undeterministic, the refund would silently age out of
its window mid-week, and the suite would stop being reproducible, which is the
whole reason entry 36 froze it. Moving `today` alone and re-blessing whatever
came out — that is how a deformed demo gets blessed as correct, and the
transcripts would have recorded it without complaint. Shifting `member_since`
with everything else — an eighteen-day, now seventeen-day, drift on a tenure
fact from 2021 is churn with no meaning, and it accumulates every time.

**Answers the question.** "How do you know the dataset still says what it said
last month?" Because the diff is all dates and nothing else. Ten lines changed
across six transcripts, every one a reply carrying a date; no envelope field,
reason code or idempotency key moved. `evidence/demo_transcript.txt` and
`evidence/injection_transcript.txt` were re-captured and then checked against
live runs rather than hand-edited, and the injection capture still emits key
`929b981a…`, which is the property that file exists to prove.

**Lives in.** `profiles/bookly.json` (`clock.today` and every dated order), the
nine date assertions in `tests.py`, the six golden transcripts,
`evidence/`, and `docs/images/console-operator-view.png`. Issues #70 and #73.
