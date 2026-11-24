# Blind labelling packs (T171)

Hand-label packs for real PRs that are not yet `labels_reviewed` rows.

**Blind protocol:** fill `root` / `propagated` / `unaffected` from the diff and (when you
have them) run outputs only. Do **not** look at lgcov's labels, bench JSON, or prior
`expected:` blocks while labelling.

After review, promote a pack into a `docs/benchmark/*.yaml` row with
`status: labels_reviewed` (see `open_deep_research-163.yaml`).

| Pack | PR | Status |
|---|---|---|
| [gemini-fullstack-90.md](gemini-fullstack-90.md) | google-gemini/gemini-fullstack-langgraph-quickstart#90 | blank — human |
| [gemini-fullstack-35.md](gemini-fullstack-35.md) | google-gemini/gemini-fullstack-langgraph-quickstart#35 | blank — human |
| [local-deep-researcher-81.md](local-deep-researcher-81.md) | langchain-ai/local-deep-researcher#81 | blank — human |
