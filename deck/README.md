# Deck generator

Two decks, two audiences, both generated, neither hand-edited.

| | `npm run build` | `npm run build:lean` |
| --- | --- | --- |
| Writes | `../Bookly_Agent_Architecture.pptx` | `../Bookly_Customer_Deck.pptx` |
| Source | `build.js` | `build-lean.js` |
| Slides | 5 | 4 |
| Audience | An engineering reviewer — depth, tradeoffs, what broke in the original build | A customer-facing audience — the claim, one live turn, the one bug this build actually caught and fixed, and how a pilot gets scoped |

```bash
cd deck && npm install && npm run build         # the technical deck
cd deck && npm install && npm run build:lean     # the customer deck
```

Both write into the repo root, including all speaker notes. The lean deck's
palette constants are a deliberate, verbatim duplicate of `build.js`'s —
same purple-means-deterministic rule, same fonts, same content width — kept
as two copies rather than one shared module, so a change to one build
target can never silently move the other one's slides.

**This is build tooling only.** The agent itself still runs with no
dependencies and no API key — `npm` is never needed to run the demo or the
tests. Nothing under `deck/` is imported by any Python module.

Every transcript excerpt on either deck's slides is copied verbatim from
`../evidence/`, with elisions marked `...`. If you change the evidence,
re-check the slide text against it.
