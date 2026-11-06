# Bake-off: did this PR break my agent?

What seven existing tools cover for that question, next to lgcov, as their own docs and repos
described them on 5 Nov 2026. Nothing was installed or run, so this compares what is
documented, not how well it works.

Reenact, EvalView, graphtrace, AgentAssay and statgate are in
[why-us.md](why-us.md#related-tools). rubric-eval, Shadow and Evalgate aren't compared in
either doc, and this one makes no claims about them.

## How the cells are scored

| Column | Yes | Partial |
|---|---|---|
| PR-level | A documented flow runs on a pull request and compares the PR with base, by running both or against a stored base result | Runs on a PR but gates on a fixed threshold, or you write the comparison |
| Node-aware | The PR result reports per LangGraph node | Nodes can be traced or scored, but you wire it, or the PR result doesn't show them |
| Root vs propagated | Names the node that caused a change and separates those that only changed downstream | Some root-cause or first-divergence output, not split by node that way |
| Calibrated confidence | The verdict uses a test or interval between base and PR, or agreement with human labels decides what a judge may do | Repeats, pass rates, agreement numbers or intervals exist but don't decide the PR result |
| Human loop | Review or override is built in and feeds back into the evals | A review step exists, but nothing carries its result to a later PR's evals |
| Per-PR CI time | A number the vendor publishes, or for lgcov one it measured | Not used: a timeout isn't a duration, so otherwise Unknown |

**No** means a source shows the mechanism rules it out, such as an action whose documented
output is only per-score deltas. **Unknown** means the sources are silent. lgcov is scored by
the same rules, and its notes carry its limits.

## The table

The columns are lgcov's own framing of the problem, so they favour it. A Yes means the mechanism
exists, not that it is proven: read lgcov's "pilot" and "built" with the notes further down, and
see [Where others are ahead](#where-others-are-ahead) for what these columns leave out.

| Tool | PR-level | Node-aware | Root vs propagated | Calibrated confidence | Human loop | Per-PR CI time |
|---|---|---|---|---|---|---|
| LangSmith | Partial | Partial | Partial | Partial | Yes | Unknown |
| Opik | Partial | Partial | Partial | Partial | Yes | Unknown |
| Kitaru | Yes | Partial | Partial | Partial | Yes | Unknown |
| Langfuse | Partial | Partial | No | Partial | Yes | Unknown |
| Braintrust | Yes | Partial | Partial | Partial | Yes | Unknown |
| promptfoo | Partial | Partial | No | Partial | Partial | Unknown |
| DeepEval | Partial | Partial | No | Partial | Yes, hosted | Unknown |
| lgcov | Yes | Yes | Yes, pilot | Yes, built | Yes, built | Measured, one PR |

The same table, with the evidence for each cell:

| Tool | PR-level? | Node-aware? | Root vs propagated? | Calibrated confidence? | Human loop? | Per-PR CI time |
|---|---|---|---|---|---|---|
| **LangSmith** offline evals + CI gates | **Partial.** The [example workflow][ls-ci] runs evals on each PR. Its [report script][ls-report] compares averages with fixed thresholds, with no base run. | **Partial.** Docs show [scoring one node][ls-node], and the example [does it][ls-node-ex]. The PR comment lists feedback keys, not nodes. | **Partial.** [Engine][ls-engine] diagnoses a root cause for production issues. Nothing found that labels nodes between base and PR. | **Partial.** [Repetitions][ls-rep] and [regression counts][ls-compare] per feedback key. No test or interval found. | **Yes.** [Annotation queues][ls-queues], and [corrections become few-shot examples][ls-fewshot] for the judge. | **Unknown.** The example's eval job has a [30-minute timeout][ls-ci]. |
| **Opik** Test Suites | **Partial.** The [README][op-readme] offers a PyTest integration for CI, and `run_tests` returns a [pass rate][op-result]. You write the comparison; no PR workflow found. | **Partial.** LangGraph runs are [traced][op-lg], and a suite's judge [can open the spans][op-suites]. Results are per assertion. | **Partial.** [Ollie][op-ollie] finds a root cause for one failing trace and can compare traces. Not base vs PR. | **Partial.** [`runs_per_item` and `pass_threshold`][op-policy], and [judge alignment][op-align] tunes a judge to human scores. No test or interval found. | **Yes.** [Annotation queues][op-queues] feed judge alignment and new test cases. | **Unknown.** Nothing found. |
| **Kitaru / ZenML** replay with one change | **Yes.** The [CI guide][ki-ci] replays the PR's code over a frozen cohort and compares with recorded baselines. The gate is the run's exit code; what counts as a regression is [your policy][ki-judge]. | **Partial.** The [adapter][ki-lg] records graph, model and tool callbacks, and evaluators can read them. Results group by evaluation, not node. | **Partial.** [Listing both sessions' nodes][ki-replay] shows where the paths split, by hand. Nothing labels them. | **Partial.** [Baseline and result means and pass rates][ki-exp], a [held band and a written calibration protocol][ki-judge]. No test or interval found. | **Yes.** [Investigations][ki-inv] store annotations. Evaluators are checked against them, and confirmed sessions become cohorts. | **Unknown.** The guide's `--timeout 1800` is a limit. |
| **Langfuse** experiment-action | **Partial.** The [action][lf-action] comments on the PR, and your script raises `RegressionError` at your threshold. Docs give [code you copy][lf-baseline] to check an approved baseline in your repo. | **Partial.** LangGraph runs are [traced][lf-lg], and [observation-level evaluators][lf-judge] score single operations. The [comment][lf-body] has run scores and item rows. | **No.** The [comment][lf-body] has no node or base comparison. Docs send you to [the failing item's trace][lf-compare]. | **Partial.** [Score Analytics][lf-kappa] gives Cohen's kappa, F1 and agreement, with a [calibration guide][lf-calib]. Neither decides the PR result. | **Yes.** [Annotation queues][lf-queues] and human scores. Reviewed verdicts can become the [approved baseline][lf-baseline]. | **Unknown.** Nothing found. |
| **Braintrust** eval-action | **Yes.** The [action][bt-action] comments per-score improvements and regressions [against a baseline experiment][bt-compare]. Failing on a regression needs [a custom reporter][bt-ci]. | **Partial.** LangGraph logs [one span per node][bt-lg], but the comment is per score and [comparisons stay at trace level][bt-traces]. | **Partial.** [Loop][bt-loop] can diagnose a failing trace. The [comment code][bt-main] only has per-score deltas. | **Partial.** [Trials][bt-trials] measure variance, and the comment shows deltas and counts. No test or interval found. | **Yes.** [Human review][bt-review] with [several reviewers][bt-multi], feeding datasets. | **Unknown.** Nothing found. |
| **promptfoo**-action | **Partial.** Its [README][pf-action] says it evaluates only the current checkout, with no base run. Pass/fail counts and `fail-on-threshold`. | **Partial.** [Trace and trajectory assertions][pf-assert] work when the [provider][pf-lg] emits spans. The comment is aggregate. | **No.** One checkout and aggregate counts leave nothing to attribute. | **Partial.** [`repeat-min-pass`][pf-action] per test, and a [Wilson 95% interval][pf-wilson] on repeat stability, within one version. | **Partial.** The [web viewer][pf-ui] takes pass, fail, score and comment. They reach exports, not a later PR. | **Unknown.** Nothing found. |
| **DeepEval** in CI | **Partial.** `deepeval test run` [fails the build][de-ci] on a metric threshold. Comparing with an [`--official` run][de-official] needs Confident AI, its hosted side. | **Partial.** [Nodes become spans][de-lg], and you [stage a metric on one][de-node]. You wire each, and it's [single-turn only][de-comp]. | **No.** The CLI reports per-metric pass or fail. Nothing found on Confident AI either. | **Partial.** Thresholds and a [flaky marker][de-flaky] in CI. [Eval Alignment][ca-align] on Confident AI reports agreement and a confusion matrix; you decide what gates. | **Yes, hosted.** [Annotation and verdict validation][ca-hitl] on Confident AI. Nothing in the open-source CLI. | **Unknown.** Nothing found. |
| **lgcov** | **Yes.** The [Action][lg-action] replays the PR against a committed cassette and comments. Base isn't re-run on the PR. | **Yes.** The comment has a row per node. A [subgraph counts as one node][lg-gaps], so nodes inside it can't be named. | **Yes, pilot.** The [first node to drift][lg-root] in each run is root, later ones downstream. [Root precision 1 of 1 on one PR][lg-real], 95% Wilson interval 0.21 to 1.00. | **Yes, built.** [Paired test per KPI][lg-gate], and a judge's band follows its [TPR and TNR lower bounds][lg-why]. [Not yet judged on a real PR][lg-kpis]. | **Yes, built.** [`lgcov review override`][lg-why] saves a label in the repo, and the next PR re-estimates the judge. [Labels so far are the demo's][lg-labels]. | **216 s and 538 s** on [one PR][lg-real], capped settings. [Under 1 s on the demo][lg-speed], no real model. |

## Where lgcov is different (evidence-backed)

Against these seven, as documented. Each has a limit next to it.

- **Root vs propagated per node, between base and PR.** None of the seven labels graph nodes
  this way. The nearest are Kitaru's node listings, which you read by hand, and root-cause
  diagnosis of a failing trace in LangSmith Engine, Opik's Ollie and Braintrust's Loop.
  lgcov's evidence is a [pilot][lg-real], not proof: one PR, and 1 of 1 roots. Recall is
  capped at about 1/5 to 1/6, because only the first node to drift is called root (T162), and
  a subgraph is scored as one node. A noise check, base against base on a real model, is
  planned (T173). Until then these runs can't separate cause from model noise.
- **A judge earns the right to block.** lgcov lets a judge block only when the lower bounds of
  its TPR and TNR against human labels clear 0.80. Langfuse reports kappa and F1, Confident AI
  a confusion matrix and Kitaru a calibration protocol, and LangSmith and Opik tune judges from
  corrections, but none documents a gate that reads such bounds. This is built, but it hasn't
  judged a real PR: `lgcov judge` replays the graph, so judging costs live calls, and the Jev
  judge is untested at this payload size.
- **A paired test per KPI in the PR result.** None of the seven documents a test or interval
  between base and PR. Braintrust and Kitaru show deltas, and promptfoo's Wilson interval is
  within one version. This isn't unique to lgcov: [statgate and AgentAssay](why-us.md#related-tools)
  do it too. On the one real PR, with one case and one run per side, the results
  [aren't gate verdicts][lg-kpis].
- **Serving by node.** lgcov serves an unchanged node from the cassette, by its code fingerprint
  and masked input, so a change only to a masked value can be served stale
  ([known limits](../README.md#known-replay-limits)). LangSmith's [pytest cache][ls-cache]
  replays HTTP calls. Kitaru re-runs the agent
  [from the top][ki-concept], and on LangGraph it answers tool calls from recordings only for
  [factory-built agents][ki-lg]. Limits: a node holding a compiled subgraph always runs live,
  so a live model's new answer can read as drift. A comment or docstring edit currently changes
  a node's fingerprint, so that node runs live. `Document`s and custom pydantic objects inside
  state fields can't be saved yet. Speed is [measured only on the demo][lg-speed].
- **Labels in the repo, no server.** A reviewer's override is a label in git, read back on the
  next PR. LangSmith, Opik, Langfuse, Braintrust and Confident AI keep review data on a platform,
  though Opik and Langfuse can be self-hosted, and Kitaru needs [its own server and
  workers][ki-deploy]. Keeping things in the repo isn't unique: Langfuse's approved baseline
  lives there too. The labels so far are the demo's.

## Where others are ahead

They do better:

- **LangSmith.** [Engine][ls-engine] turns production traces into tracked issues, fix PRs and
  dataset examples. Its [fix validation][ls-fix] (private beta, LangSmith Cloud deployments only)
  replays a known issue's traces on a baseline deployment and on a PR preview, with a verdict per
  trace. That's the closest thing here to base vs PR on a LangGraph agent, though by trace, not
  by node, and only for Engine's own fix PRs.
- **Opik.** A failing production trace [becomes a regression case][op-suites] with a
  natural-language assertion, from the trace view. It can also [optimize a judge prompt][op-align]
  toward human scores.
- **Kitaru.** Real sessions become [frozen, versioned cohorts][ki-ci], and they can be imported
  from other tracing tools. Tool calls [answered from recordings][ki-concept] keep replays away
  from real systems. Its [calibration protocol][ki-judge] is the most careful one read here:
  separate development and test sets, with held results reported.
- **Langfuse.** [Kappa, F1 and agreement][lf-kappa] between judge and human with no setup, and a
  written [per-case policy][lf-baseline] that no approved pass may regress.
- **Braintrust.** A ready-made PR comment of [improvements and regressions per score][bt-action],
  plus [diff mode][bt-compare] and [several reviewers][bt-multi], blind review included.
- **promptfoo.** No server. It [skips the eval][pf-action] when watched prompts and their
  dependencies haven't changed. [`repeat` runs each test several times][pf-action] on a PR, where
  lgcov's PR tier runs each case once.
- **DeepEval.** A broad metric library in pytest, [metrics on single spans][de-comp] including a
  [LangGraph node][de-node], [trajectory evals][de-lg], and TypeScript too. The CLI needs no server.
- **All seven** work beyond LangGraph, and most score production traffic and have dashboards.
  lgcov is LangGraph only, has neither, and has scored one real PR.

## How this was checked

- Read on 5 Nov 2026, from each tool's own docs, repo and examples; no blogs or comparison sites.
  Repo links are pinned to the commit read. Vendor docs aren't pinned and can change.
- Read, not run: no installs, accounts, keys or model calls. A closed platform can do more than
  its docs say, so No and Unknown mean not in what is documented.
- LangSmith: the docs call the example's releases quality-gated, but at this commit its
  [report script][ls-report] exits 0 on a missed threshold, so a miss shows in the comment and
  doesn't fail that job. LangSmith's [pytest plugin][ls-pytest] can fail a test on an assertion.
- promptfoo: a [docs page][pf-docs] still describes a before-and-after run. The table follows the
  action's own [README][pf-action], which says otherwise and matches its [code][pf-main].
- lgcov: [benchmark.md][lg-real] for the real-PR numbers, and this repo at `f5b313c` for the rest.
  The 216 s and 538 s cover both sides of `lgcov bench`, not the Action, whose PR tier has a
  [90 s budget][lg-action].

[ls-ci]: https://github.com/langchain-ai/cicd-pipeline-example/blob/5ab0d0a0f3c90999f786f834d9c05484dfe5f2bf/.github/workflows/test-with-results.yml
[ls-report]: https://github.com/langchain-ai/cicd-pipeline-example/blob/5ab0d0a0f3c90999f786f834d9c05484dfe5f2bf/.github/scripts/report_eval.py
[ls-node-ex]: https://github.com/langchain-ai/cicd-pipeline-example/blob/5ab0d0a0f3c90999f786f834d9c05484dfe5f2bf/tests/offline_evals/test_sql_generation.py
[ls-node]: https://docs.langchain.com/langsmith/evaluate-graph#running-and-evaluating-individual-nodes
[ls-engine]: https://docs.langchain.com/langsmith/engine
[ls-fix]: https://docs.langchain.com/langsmith/engine-fix-validation
[ls-rep]: https://docs.langchain.com/langsmith/repetition
[ls-compare]: https://docs.langchain.com/langsmith/compare-experiment-results#view-regressions-and-improvements
[ls-queues]: https://docs.langchain.com/langsmith/annotation-queues
[ls-fewshot]: https://docs.langchain.com/langsmith/create-few-shot-evaluators
[ls-pytest]: https://docs.langchain.com/langsmith/pytest
[ls-cache]: https://docs.langchain.com/langsmith/pytest#caching
[op-readme]: https://github.com/comet-ml/opik/blob/308cc5bad41c76604659e6a5975d78e9cec7a84b/README.md
[op-result]: https://github.com/comet-ml/opik/blob/308cc5bad41c76604659e6a5975d78e9cec7a84b/sdks/python/src/opik/api_objects/dataset/test_suite/test_suite_result.py
[op-lg]: https://www.comet.com/docs/opik/integrations/langgraph
[op-suites]: https://www.comet.com/docs/opik/evaluation/advanced/building-test-suites
[op-policy]: https://www.comet.com/docs/opik/evaluation/advanced/building-test-suites#execution-policies
[op-ollie]: https://www.comet.com/docs/opik/tracing/debug-agents
[op-align]: https://www.comet.com/docs/opik/evaluation/judge_alignment
[op-queues]: https://www.comet.com/docs/opik/evaluation/annotation_queues
[ki-ci]: https://github.com/zenml-io/kitaru/blob/82fd53d16f406fff92eb19a68f088dfa68488af3/docs/book/guides/regression-suite.md
[ki-judge]: https://github.com/zenml-io/kitaru/blob/82fd53d16f406fff92eb19a68f088dfa68488af3/docs/book/guides/judge-evaluations.md
[ki-lg]: https://github.com/zenml-io/kitaru/blob/82fd53d16f406fff92eb19a68f088dfa68488af3/docs/book/adapters/langgraph.md
[ki-replay]: https://github.com/zenml-io/kitaru/blob/82fd53d16f406fff92eb19a68f088dfa68488af3/docs/book/guides/replay-and-overrides.md
[ki-concept]: https://github.com/zenml-io/kitaru/blob/82fd53d16f406fff92eb19a68f088dfa68488af3/docs/book/concepts/replay.md
[ki-exp]: https://github.com/zenml-io/kitaru/blob/82fd53d16f406fff92eb19a68f088dfa68488af3/docs/book/concepts/experiments.md
[ki-inv]: https://github.com/zenml-io/kitaru/blob/82fd53d16f406fff92eb19a68f088dfa68488af3/docs/book/concepts/investigations.md
[ki-deploy]: https://github.com/zenml-io/kitaru/blob/82fd53d16f406fff92eb19a68f088dfa68488af3/docs/book/deploy/README.md
[lf-action]: https://github.com/langfuse/experiment-action/blob/e4a07282587d1fa3c682aaea33fbb66b3d3e108b/README.md
[lf-body]: https://github.com/langfuse/experiment-action/blob/e4a07282587d1fa3c682aaea33fbb66b3d3e108b/src/comment/body.ts
[lf-baseline]: https://langfuse.com/docs/evaluation/experiments/experiments-ci-cd#approved-baseline
[lf-compare]: https://langfuse.com/docs/evaluation/experiments/compare-experiments#review-failures
[lf-lg]: https://langfuse.com/integrations/frameworks/langchain
[lf-judge]: https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge
[lf-kappa]: https://langfuse.com/docs/evaluation/scores/score-analytics
[lf-calib]: https://langfuse.com/guides/llm-as-a-judge-calibration-skill
[lf-queues]: https://langfuse.com/docs/evaluation/evaluation-methods/annotation-queues
[bt-action]: https://github.com/braintrustdata/eval-action/blob/ed007eb24e933007a4079bd56643bc72d3cb016f/README.md
[bt-main]: https://github.com/braintrustdata/eval-action/blob/ed007eb24e933007a4079bd56643bc72d3cb016f/eval/src/main.ts
[bt-compare]: https://docs.braintrust.dev/evaluate/compare-experiments
[bt-ci]: https://docs.braintrust.dev/evaluate/run-in-ci
[bt-lg]: https://docs.braintrust.dev/integrations/agent-frameworks/langgraph
[bt-traces]: https://docs.braintrust.dev/evaluate/interpret-results
[bt-loop]: https://docs.braintrust.dev/loop/capabilities
[bt-trials]: https://docs.braintrust.dev/evaluate/advanced-evaluations
[bt-review]: https://docs.braintrust.dev/annotate/human-review
[bt-multi]: https://docs.braintrust.dev/annotate/human-review/multiple-reviewers
[pf-action]: https://github.com/promptfoo/promptfoo-action/blob/6a8612f15670b4d5a4d43e0d6fb221d84ab389d8/README.md
[pf-main]: https://github.com/promptfoo/promptfoo-action/blob/6a8612f15670b4d5a4d43e0d6fb221d84ab389d8/src/main.ts
[pf-docs]: https://github.com/promptfoo/promptfoo/blob/69f84c38377fb3a4d65de797c0c396804214045b/site/docs/integrations/github-action.md
[pf-assert]: https://github.com/promptfoo/promptfoo/blob/69f84c38377fb3a4d65de797c0c396804214045b/site/docs/configuration/expected-outputs/index.md
[pf-lg]: https://github.com/promptfoo/promptfoo/blob/69f84c38377fb3a4d65de797c0c396804214045b/site/docs/guides/evaluate-langgraph.md
[pf-wilson]: https://github.com/promptfoo/promptfoo/blob/69f84c38377fb3a4d65de797c0c396804214045b/site/docs/guides/evaluate-llm-temperature.md
[pf-ui]: https://github.com/promptfoo/promptfoo/blob/69f84c38377fb3a4d65de797c0c396804214045b/site/docs/usage/web-ui.md
[de-ci]: https://github.com/confident-ai/deepeval/blob/fec0da671d861ba8a8b15c7679bad380be1945f6/docs/content/docs/evaluation-unit-testing-in-ci-cd.mdx
[de-official]: https://github.com/confident-ai/deepeval/blob/fec0da671d861ba8a8b15c7679bad380be1945f6/docs/content/docs/evaluation-unit-testing-in-ci-cd.mdx#marking-an-official-run
[de-flaky]: https://github.com/confident-ai/deepeval/blob/fec0da671d861ba8a8b15c7679bad380be1945f6/docs/content/docs/evaluation-unit-testing-in-ci-cd.mdx#handling-flaky-test-cases
[de-lg]: https://github.com/confident-ai/deepeval/blob/fec0da671d861ba8a8b15c7679bad380be1945f6/docs/content/integrations/frameworks/langgraph.mdx
[de-node]: https://github.com/confident-ai/deepeval/blob/fec0da671d861ba8a8b15c7679bad380be1945f6/docs/content/integrations/frameworks/langgraph.mdx#evaluate-a-node-instead-of-the-whole-graph
[de-comp]: https://github.com/confident-ai/deepeval/blob/fec0da671d861ba8a8b15c7679bad380be1945f6/docs/content/docs/evaluation-component-level-llm-evals.mdx
[ca-align]: https://www.confident-ai.com/docs/human-in-the-loop/eval-alignment
[ca-hitl]: https://www.confident-ai.com/docs/human-in-the-loop/introduction
[lg-action]: ../action.yml
[lg-root]: https://github.com/AMOHYZ/langgraph-regression-guard/blob/f5b313c8de32e415770cd78804bbe3865ec40915/src/lgcov/cli.py#L286-L307
[lg-gaps]: benchmark.md#known-gaps
[lg-real]: benchmark.md#real-prs
[lg-kpis]: benchmark.md#agent-kpis
[lg-gate]: gate.md
[lg-why]: why-us.md#what-lgcov-does-differently
[lg-labels]: ../calibration/labels/demo.csv
[lg-speed]: benchmark-speed.md
