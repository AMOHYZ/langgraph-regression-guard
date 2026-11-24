# Blind pack: gemini-fullstack-langgraph-quickstart#35

**Status:** `labels_pending_human_review`  
**PR:** https://github.com/google-gemini/gemini-fullstack-langgraph-quickstart/pull/35  
**Title:** Fix(docs): Correct typos and grammatical inconsistencies in docstrings/prompts  
**Base:** `fddf107e0a9f6c1f3c45f70031b65a7138a281b1`  
**Head:** `3da4c4e4123683f691bd2c158c19102878211ba1`  
**Graph (typical):** `backend/src/agent/graph.py` — nodes `generate_query`, `web_research`,
`reflection`, `finalize_answer`.

## Diff summary

Two files:

1. **`backend/src/agent/graph.py` — `generate_query` docstring only**
   - "generates a search queries" → "generates search queries"
   - "an optimized search query" → "an optimized search queries"
   - "the generated query" → "the generated queries"
   - No executable body change in the hunk.

2. **`backend/src/agent/prompts.py` — `answer_instructions`**
   - Leading capitalisation: "you MUST include…" → "You MUST include…"
   - This string is what `finalize_answer` formats.

## What changed (by surface)

| Surface | Change | Likely node(s) that read it |
|---|---|---|
| `generate_query` docstring | Grammar only | `generate_query` (docstring; body unchanged in this PR) |
| `answer_instructions` | Capitalise "You MUST…" | `finalize_answer` |
| Other nodes / prompts | none in this PR | — |

## Labels (fill by hand — leave blank until then)

| Field | Nodes (blank for human) |
|---|---|
| `root` | |
| `propagated` | |
| `unaffected` | |

Per-node worksheet:

| node | label (root / propagated / unaffected) | why (one line) |
|---|---|---|
| `generate_query` | | |
| `web_research` | | |
| `reflection` | | |
| `finalize_answer` | | |

## Notes for the labeller

- Blind: do not consult lgcov labels or the fingerprint table in `docs/benchmark.md`.
- Decide whether a docstring-only edit counts as changing what the node *does* under the
  labelling rule you use for real rows (see `open_deep_research-163.yaml` header for the
  written rule — read it after you draft, not before, if you want a strict blind).
