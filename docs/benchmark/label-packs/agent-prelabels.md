# AGENT PRE-LABELS (10-way vote) · NOT HUMAN-REVIEWED · do not use for benchmark scores

T171. Ten agent voters, run separately, each labelled every node in the three blind packs
([gemini-fullstack-90](gemini-fullstack-90.md), [gemini-fullstack-35](gemini-fullstack-35.md),
[local-deep-researcher-81](local-deep-researcher-81.md)). These are drafts for human review,
not labels: the pack files are unchanged and still `labels_pending_human_review`.

**How the vote ran**

- Each voter got the same evidence bundle and nothing else: each PR's diff (`git diff base..head`,
  base = merge base) and the agent's source at base and head. No PR titles or descriptions, no
  pack text, no lgcov or bench output, no earlier labels, no other voter's output.
- Definitions given to every voter, word for word:
  - **root**: the PR changed this node's own code, prompt, config, or a helper/config it calls,
    and its behaviour could change on the same input. Docstring-only or comment-only edits are not root.
  - **propagated**: own code unchanged, but its input changes because an upstream node is root or propagated.
  - **unaffected**: neither.
  - **unsure**: evidence doesn't decide it; say why.
- Majority label = a label with at least 6 of 10 votes; otherwise "no majority".
- Strongest dissenting reason: from the largest group that voted against the majority (the
  runner-up when there is no majority), the vote with the longest reason plus evidence.
- Raw votes from every voter: [agent-prelabels-votes.json](agent-prelabels-votes.json).
- All ten voters are the same model with the same prompt, so a 10/10 split shows that the
  votes agree, not that ten independent reviewers checked the label.

| Pack | PR | Base | Head |
|---|---|---|---|
| gemini-fullstack-90 | https://github.com/google-gemini/gemini-fullstack-langgraph-quickstart/pull/90 | `cf240a2c8599` | `8e6f3f576feb` |
| gemini-fullstack-35 | https://github.com/google-gemini/gemini-fullstack-langgraph-quickstart/pull/35 | `fddf107e0a9f` | `3da4c4e41236` |
| local-deep-researcher-81 | https://github.com/langchain-ai/local-deep-researcher/pull/81 | `24db21faae75` | `6ca0d4625ef7` |

| pack | node | majority label | vote split | strongest dissenting reason |
|---|---|---|---|---|
| gemini-fullstack-90 | `generate_query` | root | root 10 | none (10/10) |
| gemini-fullstack-90 | `web_research` | propagated | propagated 10 | none (10/10) |
| gemini-fullstack-90 | `reflection` | propagated | propagated 10 | none (10/10) |
| gemini-fullstack-90 | `finalize_answer` | propagated | propagated 10 | none (10/10) |
| gemini-fullstack-35 | `generate_query` | unaffected | unaffected 10 | none (10/10) |
| gemini-fullstack-35 | `web_research` | unaffected | unaffected 10 | none (10/10) |
| gemini-fullstack-35 | `reflection` | unaffected | unaffected 10 | none (10/10) |
| gemini-fullstack-35 | `finalize_answer` | root | root 10 | none (10/10) |
| local-deep-researcher-81 | `generate_query` | unaffected | unaffected 10 | none (10/10) |
| local-deep-researcher-81 | `web_research` | propagated | propagated 10 | none (10/10) |
| local-deep-researcher-81 | `summarize_sources` | root | root 10 | none (10/10) |
| local-deep-researcher-81 | `reflect_on_summary` | root | root 10 | none (10/10) |
| local-deep-researcher-81 | `finalize_summary` | root | root 10 | none (10/10) |
