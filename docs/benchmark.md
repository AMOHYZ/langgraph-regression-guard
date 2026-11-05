# Attribution benchmark

Run 2026-10-31: does lgcov name the node a change broke (root) and the nodes that only inherited the damage (propagated)? Each benchmark records its cases on base, replays head, and compares lgcov's labels with hand-written ones: the 3 benchmark files in `tests/fixtures/bench`. Precision / recall / F1:

| benchmark | root | propagated | found root | expected root | time | |
|---|---|---|---|---|---|---|
| linear | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 | `b` | `b` | 0.01 s | ✓ |
| send_fanout | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 | `worker_b` | `worker_b` | 0.02 s | ✓ |
| subgraph | 0.00 / 0.00 / 0.00 | 1.00 / 1.00 / 1.00 | `sub` | `sub.inner` | 0.01 s | ❌ known gap (subgraph) |
| **micro** | 0.67 / 0.67 / 0.67 | 1.00 / 1.00 / 1.00 | | | | |
| **macro** | 0.67 / 0.67 / 0.67 | 1.00 / 1.00 / 1.00 | | | | |

## Known gaps

- `subgraph` (subgraph): lgcov said root `sub`, expected `sub.inner`. The PR edits inner, inside the subgraph node sub; after only drifts because of it.

Reproduce:

```bash
lgcov bench tests/fixtures/bench
```

## Real PRs

A pilot, not a precision guarantee: one PR, one question, one run per side each time, and lgcov named one root each time. These numbers stay out of the synthetic table above. This section is written by hand and `lgcov bench` rewrites this file, so keep it when you regenerate. The tables just below are the first run, on `gpt-4.1`. The second run, on `gpt-6-luna`, is at the end.

| | |
|---|---|
| Date | 2026-11-05 |
| lgcov commit | `8d9fd8b` |
| PR | [langchain-ai/open_deep_research#163](https://github.com/langchain-ai/open_deep_research/pull/163), `f95d200` to `6532a41` |
| Labels | [open_deep_research-163.yaml](benchmark/open_deep_research-163.yaml), reviewed by a human; its EU AI Act question only |
| Models | `gpt-4.1`; `gpt-4.1-mini` for summaries (pinned on both sides) and for compression on base (its default) |
| Settings | Clarification off, 1 research unit, 2 researcher iterations, 3 tool calls per researcher, to cap the cost |
| Versions | langgraph 1.2.16 and langchain-core 1.6.13, not the langgraph 0.6.2 the PR's lock pins |
| Validity | Checked before scoring: no tool or report errors in either side's saved run, and research notes and a final report on both |

### Benchmark KPIs

Measured by `lgcov bench`, apart from the last two rows.

| KPI | result | target |
|---|---|---|
| Root precision | 1.00 (1 of 1) | At least about 80%, the kill criterion |
| Root recall | 0.17 (1 of 6) | None; capped at about 1/6 |
| Root F1 | 0.29 | None |
| Propagated precision | 0.50 (1 of 2) | None |
| Propagated recall | 1.00 (1 of 1) | None |
| Propagated F1 | 0.67 | None |
| Time | 216 s, both sides | None |
| Spend | $0.52 (_any errors on the OpenAI usage page to be added_) | At most $0.60; stop and re-plan over $1 |
| Requests | _To be added from the OpenAI usage page_ | About 110 |

lgcov said root `write_research_brief`, propagated `final_report_generation` and `research_supervisor`.

- **Recall is capped at about 1/6.** lgcov calls only the first node to drift root, and this PR has 6 labelled roots. Multi-root is T162. This is a known limit, not a bug.
- **The propagated false positive comes from the same limit.** `research_supervisor` is the subgraph holding the other 5 labelled roots, and lgcov scores it as one node.
- **Root precision is 1 of 1,** so it says little: the 95% Wilson interval is 0.21 to 1.00.
- **The hit has a real cause, but this run can't separate cause from noise.** The supervisor's system prompt, which the PR rewrote, differs between the sides. But a node whose code changed runs live, and a live model answer differs anyway. No run compared base against itself.
- **Clarification off removes the likeliest false root.** `clarify_with_user` only renamed locals, and with clarification off it calls no model, so it can't drift. With it on, it would run live and probably be called root. That wasn't measured.

### Agent KPIs

From [taxonomy.yaml](taxonomy.yaml). `lgcov bench` doesn't judge these, so the two code checks were read from the saved runs by a script using the same pass rule. `lgcov judge` replays the graph, which would have meant more paid calls. With one case and one run per side, these aren't gate verdicts.

| KPI | what it checks | judge | base | head |
|---|---|---|---|---|
| `supervisor_delegates` | The supervisor calls `ConductResearch` at least once | code | Pass (1 call) | Pass (1 call) |
| `researcher_uses_think_tool` | The researcher calls `think_tool` at least once | code | Fail (0 calls) | Pass (1 call) |
| `report_claims_supported` | Every claim in the report is backed by the research notes | model | Not judged | Not judged |

- **`researcher_uses_think_tool` can't pass on base,** because this PR is what added `think_tool`. Head's count comes from the one "Reflection recorded" line in its research notes, because the researcher's own messages aren't saved.
- **`report_claims_supported` needs a paid model call,** so it wasn't run.

### Second run, on `gpt-6-luna`

The same PR and question on a cheaper model, because `gpt-4.1` and `gpt-4.1-mini` were ruled out on cost. It's still one PR, and lgcov named the same node, so this isn't a second data point for precision.

| | |
|---|---|
| Date | 2026-11-05 |
| lgcov commit | `8d9fd8b` |
| Models | `gpt-6-luna` for research, compression, the final report and summaries, pinned on both sides |
| Output caps | 32,000 tokens for compression and the final report; the other settings as in the first run |
| Labels | As in the first run, except that `compress_research` is propagated for this run, not root. Pinning its model removes its only evidence, and its prompts are the same on both sides. The labels file is unchanged. |
| Validity | Checked before scoring: no tool or report errors in either side's saved run, no empty notes, and research notes and a final report on both. The saved answers all came from `gpt-6-luna`. |

| KPI | this run | first run | target |
|---|---|---|---|
| Root precision | 1.00 (1 of 1) | 1.00 (1 of 1) | At least about 80%, the kill criterion |
| Root recall | 0.20 (1 of 5) | 0.17 (1 of 6) | None; capped at about 1/5 |
| Root F1 | 0.33 | 0.29 | None |
| Propagated precision | 0.50 (1 of 2) | 0.50 (1 of 2) | None |
| Propagated recall | 0.50 (1 of 2) | 1.00 (1 of 1) | None |
| Propagated F1 | 0.50 | 0.67 | None |
| Time | 538 s, both sides | 216 s, both sides | None |
| Spend | _To be added from the OpenAI usage page, with any errors it shows_ | $0.52 | Under $0.60 |
| Requests | _To be added from the OpenAI usage page_ | _To be added_ | None |

lgcov said root `write_research_brief`, propagated `final_report_generation` and `research_supervisor`, the same as the first run.

- **The changes in the scores come from the label move, not from lgcov.** It named the same nodes. Propagated recall fell because `compress_research` is inside the subgraph, where lgcov can't name a node.
- **Root precision is 1 of 1 again,** on the same PR and the same node, so it isn't 2 of 2. The 95% Wilson interval is still 0.21 to 1.00.
- **The first run's other limits still apply:** nothing here separates cause from noise, and clarification was off.
- **Head researched far less than base:** 1 block of search results and 19 sources, against 8 and 107. The first run leaned the same way, at 1 and 23 against 4 and 42. One run can't say whether that's the PR's new limits.
- **An earlier try on this model was invalid, and isn't scored here.** At the default cap of 8,192 tokens, base's compression came back empty, so its final report was written with no notes, and the run still produced a score. The most likely cause is that reasoning used up the cap, since reasoning tokens count toward it. That's why the caps above are higher, and why the validity check now fails on an empty note.

| Agent KPI | base | head |
|---|---|---|
| `supervisor_delegates` | Pass (1 call) | Pass (1 call) |
| `researcher_uses_think_tool` | Fail (0 calls) | Pass (2 calls) |
| `report_claims_supported` | Not judged | Not judged |

These were read the same way as the first run's, and `researcher_uses_think_tool` still can't pass on base.
