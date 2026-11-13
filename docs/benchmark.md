# Attribution benchmark

**5-10 PRs are a pilot, not a precision guarantee, and so far there's one PR.** On open_deep_research #163, run twice, root recall is 0.17 (1 of 6) on the pilot and 0.20 (1 of 5) on the `gpt-6-luna` rerun, so lgcov missed most of the labelled roots. Root precision is 1 of 1 (95% interval 0.21 to 1.00), and it's the same single root both times. Re-scored offline with the multi-root rule (T162), the pilot's saved runs name no root: see [Multi-root re-score](#multi-root-re-score-t162). On 72 synthetic cases from 24 edits, lgcov's root misses 3 of 31 regressed cases (1 of 12 by edit) and flags all 24 harmless ones, as do both node-naming baselines.

## What was measured

Whether lgcov names the node a change broke (root) and the nodes that only inherited the damage (propagated), scored against hand-written labels. On the real PR, agent KPIs were also read from the saved runs, kept separate from those scores. Not measured: a judge on a real PR, and how much a live model's answers vary with no change at all.

Each kind of evidence has its own section, and their numbers never combine: demo fixtures, synthetic cases and real PRs. `lgcov bench --synthetic` replaces only its own section. `lgcov bench` overwrites this whole file by default and writes only the demo fixtures table and its known gaps, so pass `--out` with another path and copy those in.

## Limitations

- **One real PR (n=1).** Both runs are the same PR and question, so they aren't two data points.
- **The edited-node baseline beats lgcov on single-node edits,** on recall, 1.00 (20 of 20) against 0.85 (17 of 20), and on coverage, 1.00 (44 of 44) against 0.86 (38 of 44). lgcov is ahead only on case-level precision, 0.39 against 0.35: see [Synthetic](#synthetic).
- **No harmless-edit discrimination yet.** lgcov and both node-naming baselines flag 24 of 24 harmless synthetic cases; negative controls (T123) and non-pinnable edits (T182) are the next tests.
- **Synthetic cases come from our own generator,** so they share its assumptions about what an edit looks like; real PRs (T171) are the check.
- **Usage counts are pending.** Request, token and error counts for the `gpt-6-luna` runs and the pilot still have to come from the usage page: see [Cost per run](#cost-per-run).
- **lgcov's root label has no noise threshold.** Base run against itself gave the same root and propagated labels as both #163 runs, so #163's 1 of 1 can't be told apart from model noise, and real-PR root precision doesn't test the kill criterion yet: see [A/A noise floor](#aa-noise-floor).
- **More real PRs (T171) are pending,** so there are no numbers for them.

## Demo fixtures

Run 2026-11-11: does lgcov name the node a change broke (root) and the nodes that only inherited the damage (propagated)? Each benchmark records its cases on base, replays head, and compares lgcov's labels with hand-written ones: the 4 benchmark files in `tests/fixtures/bench`. Precision / recall / F1:

| benchmark | root | propagated | found root | expected root | time | |
|---|---|---|---|---|---|---|
| linear | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 | `b` | `b` | 4.54 s | ✓ |
| multi_root | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 | `b`, `c` | `b`, `c` | 4.34 s | ✓ |
| send_fanout | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 | `worker_b` | `worker_b` | 4.78 s | ✓ |
| subgraph | 0.00 / 0.00 / 0.00 | 1.00 / 1.00 / 1.00 | — | `sub.inner` | 5.13 s | ❌ known gap (subgraph) |
| **micro** | 1.00 / 0.80 / 0.89 | 1.00 / 1.00 / 1.00 | | | | |
| **macro** | 0.75 / 0.75 / 0.75 | 1.00 / 1.00 / 1.00 | | | | |

### Known gaps

- `subgraph` (subgraph): lgcov said root —, expected `sub.inner`. Inconclusive: `sub` (no fingerprint: a subgraph or the like (T193)). The PR edits inner, inside the subgraph node sub; after only drifts because of it.

Reproduce:

```bash
lgcov bench tests/fixtures/bench
```

## Synthetic

Run 2026-11-13: 72 cases from 24 distinct edits on 3 offline agents, covering all 8 classes in [mutations.yaml](mutations.yaml) (31 regressed, 24 benign, 17 inert). No paid calls. These numbers never combine with the real PRs below.

- **Precision:** of the cases where a method names a node, the share where that node is the edited one and the edit regressed.
- **Recall:** of regressed cases, the share where the edited node is named.
- **Coverage:** of cases where the edit changed the run, the share where the edited node is named.
- **False flag:** of cases where the edit changed the run and nothing regressed, the share where a method names a node.

Single-node leaves out the router and fan-out edits, which change a conditional edge. All scores them against the edge's source node, the node that routes: its own output doesn't change, so a method that names it has to see the router's code.

### By case

| Edits | Method | Precision | Recall | Coverage | False flag |
|---|---|---|---|---|---|
| All | lgcov root | 0.51 (28 of 55) | 0.90 (28 of 31) | 0.89 (49 of 55) | 1.00 (24 of 24) |
| All | edited node from the diff | 0.43 (31 of 72) | 1.00 (31 of 31) | 1.00 (55 of 55) | 1.00 (24 of 24) |
| All | first divergence | 0.31 (17 of 55) | 0.55 (17 of 31) | 0.69 (38 of 55) | 1.00 (24 of 24) |
| All | end-to-end | — | 0.00 (0 of 31) | 0.00 (0 of 55) | 0.00 (0 of 24) |
| Single-node | lgcov root | 0.39 (17 of 44) | 0.85 (17 of 20) | 0.86 (38 of 44) | 1.00 (24 of 24) |
| Single-node | edited node from the diff | 0.35 (20 of 57) | 1.00 (20 of 20) | 1.00 (44 of 44) | 1.00 (24 of 24) |
| Single-node | first divergence | 0.39 (17 of 44) | 0.85 (17 of 20) | 0.86 (38 of 44) | 1.00 (24 of 24) |
| Single-node | end-to-end | — | 0.00 (0 of 20) | 0.00 (0 of 44) | 0.00 (0 of 24) |

### One vote per edit

Cases sharing an edit aren't independent, so each edit also gets one vote, by majority across its questions. Each cell gives the rate, the count and a 95% Wilson interval.

| Edits | Method | Precision | Recall | Coverage | False flag |
|---|---|---|---|---|---|
| All | lgcov root | 0.52 (11 of 21; 0.32–0.72) | 0.92 (11 of 12; 0.65–0.99) | 0.90 (19 of 21; 0.71–0.97) | 1.00 (9 of 9; 0.70–1.00) |
| All | edited node from the diff | 0.50 (12 of 24; 0.31–0.69) | 1.00 (12 of 12; 0.76–1.00) | 1.00 (21 of 21; 0.85–1.00) | 1.00 (9 of 9; 0.70–1.00) |
| All | first divergence | 0.33 (7 of 21; 0.17–0.55) | 0.58 (7 of 12; 0.32–0.81) | 0.71 (15 of 21; 0.50–0.86) | 1.00 (9 of 9; 0.70–1.00) |
| All | end-to-end | — | 0.00 (0 of 12; 0.00–0.24) | 0.00 (0 of 21; 0.00–0.15) | 0.00 (0 of 9; 0.00–0.30) |
| Single-node | lgcov root | 0.41 (7 of 17; 0.22–0.64) | 0.88 (7 of 8; 0.53–0.98) | 0.88 (15 of 17; 0.66–0.97) | 1.00 (9 of 9; 0.70–1.00) |
| Single-node | edited node from the diff | 0.42 (8 of 19; 0.23–0.64) | 1.00 (8 of 8; 0.68–1.00) | 1.00 (17 of 17; 0.82–1.00) | 1.00 (9 of 9; 0.70–1.00) |
| Single-node | first divergence | 0.41 (7 of 17; 0.22–0.64) | 0.88 (7 of 8; 0.53–0.98) | 0.88 (15 of 17; 0.66–0.97) | 1.00 (9 of 9; 0.70–1.00) |
| Single-node | end-to-end | — | 0.00 (0 of 8; 0.00–0.32) | 0.00 (0 of 17; 0.00–0.18) | 0.00 (0 of 9; 0.00–0.30) |

### What this says

- **Edited node from the diff matches or beats lgcov on recall and coverage on single-node edits:** recall 1.00 (20 of 20) against 0.85 (17 of 20), and coverage 1.00 (44 of 44) against 0.86 (38 of 44). Its false-flag rate is 1.00 (24 of 24). On precision lgcov is ahead by case, 0.39 (17 of 44) against 0.35 (20 of 57), because it stays quiet on an edit that changed nothing. By edit the two are level, as the table above shows.
- **This benchmark can't show where lgcov adds value.** lgcov's case rests on edits the diff can't pin to one node: a shared helper, a model or version swap, a shared config. Those are follow-up T182 and aren't here.
- **lgcov flags harmless changes without judges:** its false-flag rate is 1.00 (24 of 24) on single-node edits. This run scores drift only. The rate can't separate the methods: drift and the diff both name a node on every harmless change, and end-to-end's 0 only means it names no node.
- **First divergence ties lgcov on single-node edits,** because the first node to drift is lgcov's root rule there. On a router or fan-out edit lgcov names the routing node, from the router's fingerprint, and first divergence names a node downstream: recall on all edits is 0.90 (28 of 31) against 0.55 (17 of 31).
- **End-to-end names no node.** As a detector, its final answer changed on 1.00 (31 of 31) regressed cases and on 0.92 (22 of 24) harmless ones.

### A/A re-runs

Each question re-run with no edit. These runs aren't in the case count.

| Agent | Arm | Re-runs | Output flips | Outcome flips | What it is |
|---|---|---|---|---|---|
| demo | re-record | 30 | 0.00 (0 of 30) | 0.00 (0 of 30) | 0 by construction: a control, not a measurement |
| demo | served replay | 30 | 0.00 (0 of 30) | 0.00 (0 of 30) | 0 by construction: a control, not a measurement |
| demo | seeded noise | 30 | 0.17 (5 of 30) | 0.13 (4 of 30) | wiring test, noise rate set by us, not a real-world flip estimate |
| fanout | re-record | 30 | 0.00 (0 of 30) | 0.00 (0 of 30) | 0 by construction: a control, not a measurement |
| fanout | served replay | 30 | 0.00 (0 of 30) | 0.00 (0 of 30) | 0 by construction: a control, not a measurement |
| subgraph | re-record | 30 | 0.00 (0 of 30) | 0.00 (0 of 30) | 0 by construction: a control, not a measurement |
| subgraph | served replay | 30 | 0.00 (0 of 30) | 0.00 (0 of 30) | 0 by construction: a control, not a measurement |

### Noise rule

The noise rule (`drift_exceeds_noise`), on the seeded-noise agent at K=3: the recording and 3 repeats on base, 3 samples on head. Nothing runs repeats on a real agent yet: `lgcov bench` and the gate still compare one sample. It names drift only when head's nearest sample is further from base than base's samples are from each other, so it misses a shift smaller than the spread. Below the threshold a node reads `no drift detected above noise (K=3)`: not cleared, since a small regression can sit inside the spread and the KPI judge decides that. The noise here is ours (1 answer in 5 is cut short), so these rates say how the rule behaves, not what a real model would give.

| Agent | Arm | Head's edit | Runs | Named drift | Reads as |
|---|---|---|---|---|---|
| demo | no change | base against base, so every flag is a false alarm | 200 | 0.01 (1 of 200) | false alarm |
| demo | small shift | the prompt becomes friendly and detailed | 200 | 0.34 (68 of 200) | caught; missed 0.66 (132 of 200) |
| demo | large shift | the prompt ends on a word the outcome check looks for | 200 | 0.56 (112 of 200) | caught; missed 0.44 (88 of 200) |

### Limitations

- Exact match on the edited node: parent-only naming counts as a miss, so lgcov's `sub` doesn't score for an edit to `sub.inner`. That is every single-node miss lgcov makes.
- A router edit counts as a hit whenever the router's code changed and a node drifted after it. lgcov doesn't check that the run took another branch, and no case here has a router edit beside a node edit.
- Fake models and small graphs, so these numbers don't predict a real PR.
- The regression label comes from each agent's own outcome check.
- The two order questions gave every edit the same label, so the case-level numbers count some edits more than their evidence supports.

Reproduce:

```bash
uv run lgcov bench --synthetic
```

This replaces only this section. Plain `lgcov bench` rewrites the whole file, so run this again after it.

## Real PRs

A pilot, not a precision guarantee: one PR, one question, one run per side each time, and lgcov named one root each time. These numbers stay out of the sections above. This section is written by hand. The tables just below are the first run, on `gpt-4.1`. The second run, on `gpt-6-luna`, is at the end.

TODO(T171): 2-3 more hand-labelled real PRs

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
| Spend | $0.52 (errors not recorded; see [Cost per run](#cost-per-run)) | At most $0.60; stop and re-plan over $1 |
| Requests | Not recorded (see [Cost per run](#cost-per-run)) | About 110 |

lgcov said root `write_research_brief`, propagated `final_report_generation` and `research_supervisor`.

- **Recall is capped at about 1/6.** This run called only the first node to drift root, and this PR has 6 labelled roots. T162 changed that rule, but on these runs it names no root: see [Multi-root re-score](#multi-root-re-score-t162).
- **The propagated false positive comes from the same limit.** `research_supervisor` is the subgraph holding the other 5 labelled roots, and lgcov scores it as one node.
- **Root precision is 1 of 1,** so it says little: the 95% Wilson interval is 0.21 to 1.00.
- **The hit has a real cause, but this run can't separate cause from noise.** The supervisor's system prompt, which the PR rewrote, differs between the sides. But a node whose code changed runs live, and a live model answer differs anyway. A later base-against-base run gave the same labels, so this hit can't be told apart from noise: see [A/A noise floor](#aa-noise-floor).
- **Clarification off removes the likeliest false root.** `clarify_with_user` only renamed locals, and with clarification off it calls no model, so it can't drift. With it on, it would run live and probably be called root. That wasn't measured.

### Multi-root re-score (T162)

T162 calls a node root when its own fingerprint changed and it drifts when run with head's code on base's recorded input. That run is a probe with the network blocked, so it costs nothing. A node that reaches for the network would need a live call, and the [A/A run](#aa-noise-floor) shows one live sample drifts with no change, so it's inconclusive until T211 adds a noise threshold. The first node to drift is still reported, as `first_drift`.

Scored from the pilot's saved runs with `lgcov bench docs/benchmark --only open_deep_research-163 --from-runs <stage1/runs>`, no call made. Saved runs can't be probed, so every changed node that drifted is inconclusive here:

| | first drift (before) | T162 (after) |
|---|---|---|
| Root precision / recall / F1 | 1.00 / 0.17 / 0.29 | no root named / 0.00 / 0.00 |
| Propagated precision / recall / F1 | 0.50 / 1.00 / 0.67 | no node named / 0.00 / 0.00 |
| Root | `write_research_brief` | none |
| Propagated | `final_report_generation`, `research_supervisor` | none |
| Inconclusive | | `write_research_brief`, `final_report_generation` (not probed), `research_supervisor` (no fingerprint) |

- **Five of the six roots can't be reached by any rule yet.** They're inside the `research_supervisor` subgraph, whose inner nodes aren't recorded (T193).
- **`write_research_brief` is a model node.** After masking, it gets the same input on both sides and drifts, but that drift is one live sample. A probe would need a live call, so it stays inconclusive until T211.
- **`final_report_generation` is now inconclusive, not propagated.** Its code changed and its input changed, and without a probe on base's input lgcov can't say which one made it drift.
- **Docstring edits are a known false-root source.** The fingerprint still includes docstrings (T212), so a node whose only change is a docstring is probed, and a probe that drifts would be called root.

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
| Spend | About $0.16 ([how](#cost-per-run)) | $0.52 | Under $0.60 |
| Requests | Not recorded (see [Cost per run](#cost-per-run)) | Not recorded (see [Cost per run](#cost-per-run)) | None |

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

### A/A noise floor

#163's base, `f95d200`, run against itself with `lgcov bench --live`, so head ran every node live and nothing was served. No code differs, so any label is noise. Same question and settings as the `gpt-6-luna` run above; written down before the run: pass if lgcov labels no root or reports INCONCLUSIVE, fail on any root.

| Date | lgcov commit | Validity | Root | Propagated | Verdict | Time | Spend |
|---|---|---|---|---|---|---|---|
| 2026-11-10 | `98c6587` | Valid; all saved answers from `gpt-6-luna` | `write_research_brief` | `final_report_generation`, `research_supervisor` | Fail | 757 s, both sides | $0.13 |

- **These are exactly the labels both #163 runs gave.** `write_research_brief` is the first node that calls a model, and drift is an exact match, so it's called root whenever it runs live, PR or no PR. #163's 1 of 1 is a root by construction, not evidence that lgcov found the cause.
- **So the kill criterion can't be read from real PRs yet.** With no change, the false-root rate on this agent is 1 of 1. On a PR that makes the first model node run live, root precision only shows whether the labels include that node.
- **The drift has two sources.** The model wrote a different brief (1,021 against 1,280 characters), and the searches differed: head found 140 sources to base's 72. `research_supervisor` also keeps response ids inside text (`rs_…`, `fc_…`, `call_…`), which lgcov doesn't mask, so it drifts even when the model's text matches; a free run on a stub showed that.
- **A fix is proposed, not built:** before calling a node root, run it again on base with the same recorded input, and report INCONCLUSIVE if base against base already drifts there. With exact-match drift that makes lgcov honest about model nodes but can't name one as root; that needs a distance measure and a threshold over several base samples.

## Baselines

### Comparison points

- **The base recording.** Each run compares head with base's run of the same question, at `f95d200`, one run per side.
- **The hand labels.** They were drafted from the PR's diff and reviewed by a human ([labels](benchmark/open_deep_research-163.yaml)). On the `gpt-6-luna` run, `compress_research` counts as propagated, not root, because pinning its model removes its only evidence.
- **Base against base.** The [A/A noise floor](#aa-noise-floor) run gave the same labels as #163, so nothing here separates the PR's effect from a live model's own variation.

### Naive methods

Scored on #163 from what was already on disk, the saved runs and a clone of the PR, with no paid calls. They use the same labels as lgcov, including the label move above. Each cell gives the pilot, then the `gpt-6-luna` run.

| Method | What it calls root | Root precision | Root recall | Propagated recall |
|---|---|---|---|---|
| lgcov | The first node to drift; later ones are propagated | 1 of 1, 1 of 1 | 1 of 6, 1 of 5 | 1 of 1, 1 of 2 |
| Edited node from the diff | Each node whose own function differs between the commits, ignoring formatting | 6 of 8, 5 of 8 | 6 of 6, 5 of 5 | 0 of 1, 0 of 2 |
| First divergence | The first node whose saved output differs | 1 of 1, 1 of 1 | 1 of 6, 1 of 5 | 0 of 1, 0 of 2 |
| End-to-end (final answer changed) | No node | Not scored | Not scored | Not scored |

- **Edited node from the diff beats lgcov on recall here, but picks nothing out.** This PR edits all 8 node functions, so calling every node root scores the same. Its false roots are `clarify_with_user` (renamed locals), `final_report_generation` (matters only on a retry, an error or a different key) and, on the second run, `compress_research`. The labels were drafted from the same diff, which favours this method.
- **First divergence ties lgcov on root in both runs, because first drift is lgcov's root rule.** On this PR, lgcov's root adds nothing over it. What lgcov adds is its propagated labels, at a precision of 1 of 2 in both runs.
- **End-to-end isn't scored because it names no node.** The final report differs in both runs, but there's no node to check against the labels, and the base-against-base run's final report differs too, so we can't say the difference comes from the PR.

On single-node synthetic edits, edited node from the diff matches or beats lgcov on recall and coverage, and lgcov is ahead only on case-level precision: see [Synthetic](#synthetic). lgcov's case rests on changes the diff can't pin to one node (T182, planned).

## What the tool missed

Seen in a benchmark run:

- **Roots it can't check (T162).** Since T162 every node whose own code changed and that drifts on base's recorded input is root, not just the first to drift: the `multi_root` demo fixture scores 2 of 2. On #163 it names none. Five of the six roots are inside a subgraph, which isn't recorded (T193), and checking `write_research_brief` would need a live model call, which one sample can't tell from noise (T211), so it's inconclusive.
- **Nodes inside a subgraph.** A subgraph is scored as one node. On #163 that made `research_supervisor` a propagated false positive, and it holds most of the labelled roots. The [demo fixture](#known-gaps) misses `sub.inner` the same way.

Known from the [replay audit](validation/replay-audit.md#matrix), with no benchmark case yet:

- **A root named one node late.** If a node's output changes only in an `id`, UUID or date-time, it shows no drift, and the first node whose output changes because of it is called root.

## Cost per run

From the OpenAI usage page: $0.84 in total for the 3 runs on 5 November. The page's total doesn't split the two `gpt-6-luna` runs, so their figure is the average of what's left after the pilot.

| Run | Cost | Pending |
|---|---|---|
| Pilot, `gpt-4.1` | $0.52 | Requests, tokens, errors |
| `gpt-6-luna`, invalid and not scored | About $0.16 | Requests, tokens, errors |
| `gpt-6-luna`, scored | About $0.16 | Requests, tokens, errors |
| A/A noise floor, `gpt-6-luna`, 10 November | $0.13 | Requests, tokens, errors |

## Rounds

A [benchmark round](benchmark-rounds.md) locks a run's commits and cases, and an exposure ledger records which cases each candidate has seen. `lgcov round check` fails if a locked SHA moved or a case was already exposed to that candidate in another round. [The #163 round](benchmark/rounds/t101-163/lgcov-round.yaml) is locked retroactively: its runs came first.
