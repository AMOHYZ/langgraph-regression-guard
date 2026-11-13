# Why lgcov

lgcov replays your agent, re-runs only the nodes a PR can reach, and shows which node's
output changed first and which changed after it. Each KPI in your taxonomy gets a paired
test, and a judge can block only once it has earned that against human labels. The one
thing here no other tool found does is that node cause, root against propagated, from
replay, and it is unvalidated: on real agents it is a [pilot](benchmark.md#real-prs) on one
PR with 6 labelled roots, and a [base-against-base run](benchmark.md#aa-noise-floor) gave
the same root, so it has no noise threshold yet. First report in 2 minutes (L1 only,
measured 27 Oct 2026). Other tools as of 31 Oct 2026; Kitaru and LangSmith as of 5 Nov;
the tools added on 11 Nov as of that day.

## What's still open

Every piece of lgcov exists somewhere else on its own. The claim left is the bundle: (1) a
sticky PR comment, (2) a three-way statistical verdict, (3) node cause that holds up under
replay and (4) the judge's TPR and TNR inside the gate. [evalgate-ci](#related-tools) ships
(1), a paired test against a baseline that can read inconclusive, and (4) in one Action;
[statgate](#related-tools) ships (1) and (2). No tool found ships (3), so the claim rests
entirely on (3), which is unvalidated (see (a) below).

What could close it: teams already assemble (1), (2) and (4) from public recipes, such as
OpenRouter's tutorial on gating pull requests on evals (1 Oct 2026), and nothing durable
stops LangSmith or Braintrust shipping the bundle within a quarter. Distribution is lopsided
too: `@evalgate/sdk` had 1,724 npm downloads from 11 Oct to 9 Nov, against 6 a month on PyPI
for the step-attribution package below (both read 11 Nov).

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

**(a) Root vs propagated drift, per node.** When a PR changes an agent, the PR comment calls a
node root when its own code changed and it drifts on its recorded input (T162), and the nodes that
drift only because their input did propagated. A node whose router changed is root too, when a node drifts after it (T175): the comment says `routing change at <node>`. That rule is measured on synthetic edits only. A changed model node is inconclusive until T211. On [open_deep_research #163](benchmark.md#real-prs), `write_research_brief`
writes the rewritten supervisor prompt into the state. lgcov named it root and
`final_report_generation` propagated, as [the human labels](benchmark/open_deep_research-163.yaml)
do. It also called `research_supervisor` propagated, though that subgraph holds the other
labelled roots and is scored as one node.
*Status: built, and a pilot on real PRs.* Root precision is 1 of 1 on that one PR, with a 95%
Wilson interval of 0.21 to 1.00, so it proves no precision claim. Recall is capped at about
1/5 to 1/6. The multi-root rule (T162) names no root on the saved runs: the other roots sit inside a subgraph lgcov doesn't record (T193), and the model node is inconclusive until a noise threshold (T211).
The [replay audit](validation/replay-audit.md) checks the replay this rests on, not these
labels, and a change replay can't see can give no drift, so no root.
`diff_cassettes` classifies by each node's recorded input, but only tests call it so far. A
noise check, base against base on a real model (T173), gave the same root as the PR, so that
1 of 1 can't be told apart from model noise: lgcov's root has no noise threshold yet.
Reenact records each LangGraph node boundary in its trajectory and shows them to its judges,
but it gates per scenario, at the first call that diverges from the recording; lgcov's rule is the same
first-divergence idea, labelled per graph node. EvalView names a root tool call.
Several tools try node cause; none found ships it replay-proof in a sticky PR Action.
[SCIPE](https://github.com/garg-ankush/scipe) (Berkeley, 2024) runs an LLM judge on each
node of a compiled LangGraph graph and separates a node's own failures from those it
inherits upstream, on one version, with no base-against-PR run and no CI step.
[trajectory-causal-attribution](https://pypi.org/project/trajectory-causal-attribution/)
ablates recorded trajectory steps one at a time to find the culprit step, not a graph node.
None of the tools below labels graph nodes root or propagated between base and PR.
A plain baseline does as well: on single-node edits in the [synthetic
benchmark](benchmark.md#synthetic), naming the node the diff edited matches or beats lgcov's
root (recall 1.00 against 0.85, precision 0.35 against 0.39). With router and fan-out edits
counted, lgcov's recall is 0.90 against the diff's 1.00.

**(c) Judges that earn the right to block.** A KPI's judge may block a PR only once the
lower bounds of the 95% intervals of its TPR and TNR against human labels both clear
0.80. Below that it flags or advises: the PR comment shows its band, and `lgcov calibrate`
prints how many labels it needs to pin κ within ±0.10.
*Status: built (T063, [PR #32](https://github.com/AMOHYZ/langgraph-regression-guard/pull/32)):
`lgcov calibrate` gives TPR and TNR with Wilson intervals, and a judge's band follows their
lower bounds.* Partial, not a wedge of its own: graphtrace reports Cohen's kappa per
criterion and judgegate gives kappa with confidence intervals, evalgate-ci reports TPR and
TNR and blocks a run when the judge's kappa drops, and llm-evalgate corrects pass rates
with the judge's sensitivity and specificity. Reenact sets a criterion blocking or advisory
from its raw agreement with human labels. None found gates on interval bounds of TPR and TNR.

**(e) Calibration that lives in the repo.** A reviewer's override is a label versioned in
git, and the next PR re-estimates the judge's κ, TPR and TNR from those labels and moves
the judge between advising and blocking. No hosted service.
*Status: built (T102, [PR #33](https://github.com/AMOHYZ/langgraph-regression-guard/pull/33)):
`lgcov review override` saves a label, the judge's band moves on the next PR, and saved
cases re-judge the judge.* Partial: Kitaru also calibrates evaluators from human judgments,
on a Kitaru server you run, and the tools under (c) calibrate from labels too.

**Paired statistics, done properly.** This is standard technique, not a differentiator:
statgate, evalgate-ci and AgentAssay cover a paired or three-valued gate.
Each case's base and PR scores are paired: an exact McNemar test for binary KPIs, a
sign-flip test otherwise. Δ gets a BCa bootstrap CI, a non-inferiority margin δ decides
SHIP, BLOCK or INCONCLUSIVE, and an INCONCLUSIVE KPI shows the cases it needs to detect Δ
(N*, from Kotawala 2026, Eq. 6).
*Status: built ([PR #26](https://github.com/AMOHYZ/langgraph-regression-guard/pull/26),
[PR #27](https://github.com/AMOHYZ/langgraph-regression-guard/pull/27)).* The design
follows [statgate](https://github.com/yashchimata/statgate) (see [NOTICE](../NOTICE)),
and [AgentAssay](https://github.com/qualixar/agentassay) also gives three-valued
verdicts. What lgcov adds: one test per taxonomy KPI, each tied to the graph node it
reads, with the root drift node next to its verdict, and Holm across the blocking KPIs
with Benjamini–Hochberg across the advisory ones. Per-slice scores are built
([PR #34](https://github.com/AMOHYZ/langgraph-regression-guard/pull/34)): the report has a
table per node, one row per slice.

**Node-level memoization.** Seconds per PR is not a differentiator either. A node whose code fingerprint and masked input both match the
recording is served from the cassette, so changed nodes run live, and so do nodes whose input
changed in a part the mask leaves in. A change only to a masked value, such as a message's id
or usage, doesn't re-run the nodes below it, so they can be served stale
([known replay limits](../README.md#known-replay-limits)). A comment or docstring edit counts
as a change for now, so that node runs live too.
*Status: built ([PR #18](https://github.com/AMOHYZ/langgraph-regression-guard/pull/18));
measured on the demo in [benchmark-speed.md](benchmark-speed.md).* Not novel: Reenact, graphtrace,
[docker-agent](https://github.com/docker/docker-agent) and LangSmith's pytest cache
replay recorded calls too. lgcov's unit is the graph node.

## Related tools

LangSmith, Opik, Kitaru, Langfuse, Braintrust, promptfoo and DeepEval, scored column by
column against lgcov: [bake-off.md](bake-off.md).

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
- [Kitaru](https://github.com/zenml-io/kitaru) (ZenML, 0.27.6, Apache-2.0): records or
  imports production runs as sessions and replays them against a change, with tool calls
  answered from the recording. Human judgments calibrate evaluators, and experiments on
  frozen cohorts gate in CI. Self-hosted server, with a paid
  [hosted AI engineer](https://www.zenml.io/product/kitaru) on top.
- [LangSmith](https://docs.langchain.com/langsmith/pytest) (LangChain): a pytest plugin
  whose `LANGSMITH_TEST_CACHE` replays recorded HTTP calls, a
  [CI example](https://github.com/langchain-ai/cicd-pipeline-example) that runs evals on each
  PR against fixed thresholds, and
  [Engine](https://docs.langchain.com/langsmith/engine), which turns production traces
  into issues, evaluators and offline examples. Engine's
  [fix validation](https://docs.langchain.com/langsmith/engine-fix-validation) (private beta)
  replays an issue's traces on a baseline deployment and on a PR preview, with a verdict per
  trace. Engine also [diagnoses a root cause](https://docs.langchain.com/langsmith/engine-overview)
  from traces and connected source code and opens the fix as a pull request; not per node.
- [SCIPE](https://github.com/garg-ankush/scipe) (Berkeley; no licence file; last pushed
  Nov 2024; [LangChain's write-up](https://www.langchain.com/blog/scipe-systematic-chain-improvement-and-problem-evaluation)):
  an LLM judge on each node of a LangGraph graph, needing no ground truth, names the node
  whose independent failures hurt the final answer most. One version at a time.
- [trajectory-causal-attribution](https://pypi.org/project/trajectory-causal-attribution/)
  (1.0.0, MIT; imports as `agent_replay`): counterfactual step ablation over a recorded
  trajectory, with Shapley values and Wilson intervals, names the culprit step. No PR Action.
- [evalgate-ci](https://pypi.org/project/evalgate-ci/) (0.1.0, MIT): a GitHub Action that
  posts a sticky PR comment, reads a Wilson lower bound on the pass rate, compares with a
  baseline by McNemar's test, and checks the judge's kappa, TPR and TNR against human
  anchors. Exit 0 or 1. One of several unrelated projects named EvalGate.

## What lgcov does not do (yet)

- Agents not built on LangGraph.
- A hosted dashboard: the report lives in the PR comment, the job summary and files.
- A taxonomy without human review: KPIs come from failure modes a person has checked.
- Serve a subgraph or a `ToolNode` from the cassette: they always run live. Drift
  inside a subgraph is blamed on the subgraph node (`sub`), not its inner node; a crash
  there does name the inner node (`sub.inner`).
- Name a model node as root: since T162 a second root in the same run is named only if a
  probe with the network blocked shows it drifting, so a model node is inconclusive until T211.
- See a change that reaches a node only through a message's id, usage or other metadata, or a
  `Store` read, or replay a graph with a checkpointer or an `interrupt`:
  [known replay limits](../README.md#known-replay-limits).
- Check that a router edit changed the route: a changed router is root of any drift after its
  node, even drift a node edit caused. A router inside a subgraph isn't seen (T193), and a
  cassette recorded before routers were fingerprinted needs a re-record first.
- Tell root drift from model noise: a base-against-base run on a real model (T173) gave the
  same root as #163, so the root label has no noise threshold yet.
- Judge a real PR: the judge gate is built, but no judge has run on one yet.
- Leave a node served after a comment or docstring edit: that counts as a code change, so
  the node runs live.

## How this was checked

On 31 Oct 2026, from each tool's README and, where named, its source:

- reenact 0.2.0: `record/langchain.py` (reads `langgraph_node` into a `GraphNodeEvent`),
  `evals/judge.py` (node boundaries in the judge's transcript), `evals/baseline.py`
  (`DEFAULT_SCORE_TOLERANCE = 0.05`) and `evals/calibration.py` (agreement with human
  labels sets blocking or advisory).
- evalview 0.8.1: `core/root_cause.py` (deterministic root-tool attribution) and
  `core/types.py` (`VarianceConfig`: 10 runs, 0.8 pass rate).
- graphtrace 0.1.0, agentassay 0.2.3 and statgate 0.1.1: their READMEs. docker-agent: its
  record and replay docs.
- open_deep_research at PR #163's merge commit (`6532a41`): `tests/evaluators.py`,
  `tests/run_evaluate.py` and `.github/workflows/` (two Claude review workflows, no test
  run).
- lgcov: `cassette.py` (`replay_steps`), `fingerprint.py`, `stats.py`
  (`paired_test`, `holm`), `sampling.py` (`gate_kpis`) and `review.py`
  (`apply_decision`).
- Re-read on 5 Nov 2026: kitaru 0.27.6's README; LangSmith's pytest, Engine and fix validation
  docs and its CI example; open_deep_research's `deep_researcher.py` at `6532a41`
  (`write_research_brief` builds the supervisor's prompt); and lgcov's `cli.py` (`_run_root`)
  and `fingerprint.py`.
- Read on 11 Nov 2026, not run: SCIPE's repo and LangChain's post on it;
  trajectory-causal-attribution's, evalgate-ci's and llm-evalgate's PyPI pages and
  pypistats; `@evalgate/sdk` on npm and evalgate.com; LangSmith Engine's overview. Sources
  for the claims under "What's still open": the author's claim matrix of 7 Nov and a
  competitor report of 6 Nov, checked against those pages. judgegate is from that report,
  not re-read. Culprit and maida-assert, named in the claim matrix, weren't found and are
  left out.
- lgcov's synthetic and #163 numbers: [benchmark.md](benchmark.md) at this commit.
- Not compared live: Reenact records LangChain chat-model and tool calls through
  callbacks, and the demo agent's fake LLM is plain Python, so Reenact would record
  nothing on demo PR #7. A fair run needs the demo to call a LangChain chat model.
