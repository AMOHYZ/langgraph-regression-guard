# Blind pack: local-deep-researcher#81

**Status:** `labels_pending_human_review`  
**PR:** https://github.com/langchain-ai/local-deep-researcher/pull/81  
**Title:** Minor prompt improvements  
**Base:** `24db21faae75ccceb18b5a048e0416e2da45ab47`  
**Head:** `6ca0d4625ef7144513845928d7f32ed0ff56ada1`  
**Graph (typical):** `src/ollama_deep_researcher/graph.py` — nodes `generate_query`,
`web_research`, `summarize_sources`, `reflect_on_summary`, `finalize_summary`
(plus router `route_research`).

## Diff summary

Two files:

1. **`src/ollama_deep_researcher/graph.py`**
   - `summarize_sources`: human message templates reworded ("Search Results" → "Context",
     user topic moved into an explicit update/create instruction).
   - `finalize_summary`: whitespace in the markdown header
     (`## Summary\n\n` → `## Summary\n`).

2. **`src/ollama_deep_researcher/prompts.py`**
   - `summarizer_instructions`: goal text + new `<Task>` block about summarising context.
   - `reflection_instructions`: `<EXAMPLE>` block replaced with a `<Task>` that asks for a
     follow-up query in the same JSON shape.

## What changed (by surface)

| Surface | Change | Likely node(s) that read it |
|---|---|---|
| Summariser human template in graph | Reword Context / User Input layout | `summarize_sources` |
| `summarizer_instructions` | Goal + Task rewrite | `summarize_sources` |
| `reflection_instructions` | Example → Task wording | `reflect_on_summary` |
| `finalize_summary` string format | Blank line after `## Summary` | `finalize_summary` |
| `generate_query` / `web_research` | none in this PR | — |

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
| `summarize_sources` | | |
| `reflect_on_summary` | | |
| `finalize_summary` | | |

## Notes for the labeller

- Blind: do not consult lgcov labels or bench JSON while filling the table.
- Multi-root is allowed when several nodes' own prompts/code change.
