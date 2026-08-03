# Bookly — standing context

A support agent for a fictional bookstore. `main` is at **v4.0.1** — the whole
version-3 arc has landed, the release is cut, and a bug-fix patch (four
hosted-path defects, found and confirmed live) has landed on top of it.

## The claim, which does not change

**The language model never decides — it only converses.** The model has
exactly two jobs: extraction (turn → slots) and narration (decision →
English). `policy.py` is the only place a verdict is computed and it never
imports an LLM. Every file either enforces that boundary or demonstrates it.

## Non-negotiable constraints

- No new decision logic outside `policy.py`.
- Standard library only on the default path. `python3 web.py` must work on a
  clean clone with Python 3.9 — no pip install, no npm, no build step.
  `deck/` is the one exception and is build tooling no Python module imports.
- No network at runtime except an opt-in hosted model call.
- No framework. Vanilla JS, one stylesheet.
- `127.0.0.1` only, never `0.0.0.0`.
- All customer and agent text renders via `textContent`, never `innerHTML`.
  The forbidden APIs are enumerated in `injected_markup_is_escaped`.
- API keys never touch disk, logs, URLs, or `os.environ`.
- **Do not squash commits.** The commit history is part of what gets read.
- Colour carries provenance: purple is the deterministic side, grey is the
  model side, the customer's words are neither. It is information, not a
  brand palette, and a re-skin does not get to break it.
- Customer view *does not render* operator fields rather than hiding them.
  That is a claim about the document, not the paint.

## Where the reasoning lives

**`docs/DECISIONS.md` first** — 56 entries: the decision, the reasoning, the
alternative rejected, where it is enforced, and the sceptic's question it
answers. Several record a diagnosis that was **wrong** and later corrected;
those are the valuable ones. Do not re-litigate anything in that file without
reading its "Rejected" line.

Then, in descending fidelity: the commit messages (long, deliberately), the
43 closed GitHub issues (`gh issue view <n>` — they carry diagnosis and
corrections), the code docstrings, and `deck/build.js` speaker notes under
IF ASKED.

## Verify before you change anything

```bash
python3 tests.py          # expect 89 passed, 0 failed
/usr/bin/python3 tests.py # 3.9.6 — must also be green
python3 harness.py        # expect 9 transcripts passed
```

`python3 tests.py --count` prints the check count. Adding a check changes a
number five documents cite; `documents_state_the_actual_check_count` names
every stale line.

## Versioning and branching

The version-3 arc is **complete** and **v4.0.0 is cut**. Each item was its own
branch and sub-version, one item per branch, and all have landed:

| | | |
| --- | --- | --- |
| `v3.1.0` | cosmetics — logo, cover art, visual dressing | ✅ |
| `v3.2.0` | authorable policy parameters | ✅ |
| `v3.3.0` | the agent knowing when it does not know | ✅ |
| `v3.4.0` | the orchestration layer becoming real | ✅ |
| `v3.5.0` | full code review — simplicity, clarity, technical debt | ✅ |
| `v4.0.0` | the release, cut once all of the above landed | ✅ |

**`v4.0.1`** is a patch on top of the cut release: four defects a live demo
found on the hosted path (issues #49, #50, #51/#52, #53 — decision #52),
none of them a capability change, none of them touching `policy.py`. Not a
row in the table above — it is not an item in the planned arc, it is a
bug fix, and the table stays a record of what was planned.

Any further work keeps the same workflow: branch `claude/bookly-vN.M-<topic>`,
PR into `main`, merge with `--merge`, then tag. Embeddings for retrieval remain
**parked** — they collide with the no-dependencies constraint and would have to
be opt-in like the hosted model.

## Working style

- **Gated.** Show findings before fixing; the user makes the fix-vs-document
  calls. Plan in writing and wait for a response before writing code.
- Every bug or enhancement raised gets a GitHub issue, closed when the fix is
  pushed. Issues carry the reasoning, not just the symptom.
- Build in steps, run the checks between each, commit at each step with a
  message saying what the step proves. Every commit green at the count it
  claims.
- **Verify by running, not asserting.** For anything visual, render it and
  look — two layout defects shipped invisible in the source and obvious in
  the render.
- When an earlier diagnosis turns out wrong, say so plainly and record the
  correction. It has happened twice; both times the correction was worth more
  than the fix.

## Done means, every time

Checks green on 3.9.6 and 3.13+, CI green on all four jobs, every envelope
field in the demo scenarios unchanged against the previous tag, a clean clone
still boots with no dependencies, `docs/DECISIONS.md` extended with whatever
was decided, and `docs/wiki/Home.md` updated and published via the recipe in
`docs/wiki/README.md`.
