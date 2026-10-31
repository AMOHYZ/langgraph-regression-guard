# Why lgcov

lgcov tells you which LangGraph node a PR actually changed and which ones only inherited
the damage, gates each KPI in your taxonomy with a paired test, and lets a judge block
only once it has earned that against human labels. First report in 2
minutes (L1 only, measured 27 Oct 2026). Other tools as of 31 Oct 2026.

## The problem

A PR that changes one node gets tested for what it set out to change, and the rest of
the graph can get worse without anyone looking.
[open_deep_research#163](https://github.com/langchain-ai/open_deep_research/pull/163)
rewrote the supervisor's system prompt and the researcher's, in a PR that also added a
think tool and changed defaults. The repo had a six-evaluator suite in `tests/`
(`evaluators.py`: overall quality, relevance, structure, correctness, groundedness and
completeness), run by hand with `run_evaluate.py`. No workflow ran it on the PR, so
nothing checked what the new prompts did to the nodes downstream.

## What lgcov does differently

**(a) Root vs propagated drift, per node.** When a PR changes an agent, lgcov names the
node that drifted on its own input (root) and separates it from the nodes that only
drifted because something upstream did (propagated). In the
[PR #163 taxonomy](taxonomy.yaml), the rewritten supervisor prompt is root drift, and the
unsupported claims it causes in `final_report` are propagated.
*Status: built.* `diff_cassettes` classifies root and propagated drift from recorded node
inputs, and the PR comment lists each drifted node as root or downstream. Reenact records
each LangGraph node boundary in its trajectory and shows them to its judges, but it gates
per scenario, at the first call that diverges from the recording. EvalView names a root
tool call. None of the tools below labels graph nodes root or propagated between base and
PR. Benchmark in progress (T101).

**(c) Judges that earn the right to block.** A KPI's judge may block a PR only once the
lower bounds of the 95% intervals of its TPR and TNR against human labels both clear
0.80. Below that it flags or advises, with the number of labels it still needs printed
next to it.
*Status: built (T063, [PR #32](https://github.com/AMOHYZ/langgraph-regression-guard/pull/32)):
`lgcov calibrate` gives TPR and TNR with Wilson intervals, and a judge's band follows their
lower bounds.* Reenact sets a criterion blocking or advisory from its raw agreement with
human labels, and graphtrace reports Cohen's kappa per criterion; neither gates on
interval bounds of TPR and TNR.

**(e) Calibration that lives in the repo.** A reviewer's override is a label versioned in
git, and the next PR re-estimates the judge's κ, TPR and TNR from those labels and moves
the judge between advising and blocking. No hosted service.
*Status: built (T102, [PR #33](https://github.com/AMOHYZ/langgraph-regression-guard/pull/33)):
`lgcov review override` saves a label, the judge's band moves on the next PR, and saved
cases re-judge the judge.* Kitaru also
calibrates evaluators from human judgments, on a Kitaru server you run or on its managed
cloud.

**Paired statistics, done properly.** This is standard technique, not a differentiator.
Each case's base and PR scores are paired: an exact McNemar test for binary KPIs, a
sign-flip test otherwise. Δ gets a BCa bootstrap CI, a non-inferiority margin δ decides
SHIP, BLOCK or INCONCLUSIVE, and an INCONCLUSIVE KPI shows the cases it needs (N*, from
Kotawala 2026, Eq. 6).
*Status: built ([PR #26](https://github.com/AMOHYZ/langgraph-regression-guard/pull/26),
[PR #27](https://github.com/AMOHYZ/langgraph-regression-guard/pull/27)).* The design
follows [statgate](https://github.com/yashchimata/statgate) (see [NOTICE](../NOTICE)),
and [AgentAssay](https://github.com/qualixar/agentassay) also gives three-valued
verdicts. What lgcov adds: one test per taxonomy KPI, each tied to the graph node it
reads, with the root drift node next to its verdict, and Holm across the blocking KPIs
with Benjamini–Hochberg across the advisory ones. Per-slice scores are planned (T090).

**Node-level memoization.** A node whose code fingerprint and input both match the
recording is served from the cassette, so only changed nodes, and nodes whose input
changed because of them, run live.
*Status: built ([PR #18](https://github.com/AMOHYZ/langgraph-regression-guard/pull/18));
measured on the demo in [benchmark-speed.md](benchmark-speed.md).* Not novel: Reenact, graphtrace,
[docker-agent](https://github.com/docker/docker-agent) and LangSmith's pytest cache
replay recorded calls too. lgcov's unit is the graph node.

## Related tools

- [Reenact](https://github.com/Manas-33/Reenact) (0.2.0, MIT): records model, tool and
  MCP calls once and replays the current code against them offline. On divergence it
  fails, flags or re-runs live (capped by `--max-live`), with blocking and advisory
  criteria and a PR comment. Records LangGraph node boundaries.
- [graphtrace](https://github.com/devbyanoop/graphtrace) (0.1.0, Apache-2.0): a pytest
  plugin that records LangGraph trajectories, subgraphs included, to YAML cassettes and
  asserts on paths, state and tool calls; `graphtrace calibrate` reports Cohen's kappa
  per criterion against human labels.
- [EvalView](https://github.com/hidai25/eval-view) (0.8.1, Apache-2.0): snapshot testing
  for agents. Diffs tool names, parameters and order against a baseline, with
  deterministic root-cause categories, thresholds and pass@k runs, across frameworks.
- [AgentAssay](https://github.com/qualixar/agentassay) (0.2.3, AGPL-3.0;
  [arXiv:2603.02601](https://arxiv.org/abs/2603.02601)): stochastic testing with PASS /
  FAIL / INCONCLUSIVE verdicts, confidence intervals and SPRT, five-dimension coverage
  and mutation testing; a LangGraph adapter among ten.
- [statgate](https://github.com/yashchimata/statgate) (0.1.1, MIT): paired verdicts
  against a non-inferiority margin, SHIP / BLOCK / INCONCLUSIVE, power analysis and
  sequential early stopping, as a GitHub Action with a sticky comment, for any eval
  results. lgcov's `stats.py` follows its design.
- [Kitaru](https://github.com/zenml-io/kitaru) (ZenML, 0.27.3, Apache-2.0): records or
  imports production runs as sessions and replays them against a change, with tool calls
  answered from the recording. Human judgments calibrate evaluators, and experiments on
  frozen cohorts gate in CI. Self-hosted server or managed cloud.
- [LangSmith](https://docs.langchain.com/langsmith/pytest) (LangChain): a pytest plugin
  whose `LANGSMITH_TEST_CACHE` replays recorded HTTP calls, and
  [Engine](https://docs.langchain.com/langsmith/engine), which turns production traces
  into issues, evaluators and offline examples.

## What lgcov does not do (yet)

- Agents not built on LangGraph.
- A hosted dashboard: the report lives in the PR comment, the job summary and files.
- A taxonomy without human review: KPIs come from failure modes a person has checked.
- Attribute a crash in a router or a subgraph to a node: it counts as a harness error
  for now (T109). Subgraphs and `ToolNode`s always run live instead of being served.

## How this was checked

On 31 Oct 2026, from each tool's README and, where named, its source:

- reenact 0.2.0: `record/langchain.py` (reads `langgraph_node` into a `GraphNodeEvent`),
  `evals/judge.py` (node boundaries in the judge's transcript), `evals/baseline.py`
  (`DEFAULT_SCORE_TOLERANCE = 0.05`) and `evals/calibration.py` (agreement with human
  labels sets blocking or advisory).
- evalview 0.8.1: `core/root_cause.py` (deterministic root-tool attribution) and
  `core/types.py` (`VarianceConfig`: 10 runs, 0.8 pass rate).
- graphtrace 0.1.0, agentassay 0.2.3, statgate 0.1.1 and kitaru 0.27.3: their READMEs.
  LangSmith: its pytest and Engine docs. docker-agent: its record and replay docs.
- open_deep_research at PR #163's merge commit (`6532a41`): `tests/evaluators.py`,
  `tests/run_evaluate.py` and `.github/workflows/` (two Claude review workflows, no test
  run).
- lgcov: `cassette.py` (`replay_steps`, `diff_cassettes`), `fingerprint.py`, `stats.py`
  (`paired_test`, `holm`), `sampling.py` (`gate_kpis`) and `review.py`
  (`apply_decision`).
- Not compared live: Reenact records LangChain chat-model and tool calls through
  callbacks, and the demo agent's fake LLM is plain Python, so Reenact would record
  nothing on demo PR #7. A fair run needs the demo to call a LangChain chat model.
