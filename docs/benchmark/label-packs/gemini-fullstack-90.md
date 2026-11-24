# Blind pack: gemini-fullstack-langgraph-quickstart#90

**Status:** `labels_pending_human_review`  
**PR:** https://github.com/google-gemini/gemini-fullstack-langgraph-quickstart/pull/90  
**Title:** Fix a typo in query_writer_instructions response format  
**Base:** `cf240a2c8599eed5244d39c7343736885ddc727f`  
**Head:** `8e6f3f576febcec6a15dd6b7449fb27bf38a2c53`  
**Graph (typical):** `backend/src/agent/graph.py` — nodes `generate_query`, `web_research`,
`reflection`, `finalize_answer` (plus Send fan-out helper `continue_to_web_research`).

## Diff summary

One file, one line in `backend/src/agent/prompts.py` (`query_writer_instructions`):

- Was: "Format your response as a JSON object with ALL **three** of these exact keys"
- Now: "… ALL **two** of these exact keys"
- The listed keys were already only `rationale` and `query` (two). No other files changed.

## What changed (by surface)

| Surface | Change | Likely node(s) that read it |
|---|---|---|
| `query_writer_instructions` | Typo fix: "three" → "two" in the format sentence | `generate_query` (formats this prompt) |
| Graph wiring / other prompts | none | — |

## Labels (fill by hand — leave blank until then)

Rule of thumb (same as other real rows): a node is **root** when, on the same input, the PR
changes what *it* does (code, prompt it binds, tool, or config it reads). It is
**propagated** when the PR doesn't change it and it drifts only because its input changed.
**Unaffected** = no drift expected / not in the causal path.

| Field | Nodes (blank for human) |
|---|---|
| `root` | |
| `propagated` | |
| `unaffected` | |

Per-node worksheet (add rows if needed):

| node | label (root / propagated / unaffected) | why (one line) |
|---|---|---|
| `generate_query` | | |
| `web_research` | | |
| `reflection` | | |
| `finalize_answer` | | |

## Notes for the labeller

- Do not open lgcov bench output or any pre-filled `expected:` for this PR.
- Docstring-only vs prompt-body edits matter for fingerprint rules; this edit is in the
  prompt string the node formats, not a function docstring.
