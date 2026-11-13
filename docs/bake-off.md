# Bake-off: did this PR break my agent?

What seven existing tools cover for that question, next to lgcov, as their own docs and repos
described them on 5 Nov 2026. Nothing was installed or run, so this compares what is
documented, not how well it works.

Reenact, EvalView, graphtrace, AgentAssay and statgate are in
[why-us.md](why-us.md#related-tools). Nineteen newer tools get one row each in
[Newer tools](#newer-tools-one-row-each), thirteen read on 11 Nov 2026 and six on 13 Nov, and
not scored by the columns below. rubric-eval and Shadow aren't compared in either doc, and this one makes no claims
about them.

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
| **Kitaru / ZenML** replay with one change | **Yes.** The [CI guide][ki-ci] replays the PR's code over a frozen cohort and compares with recorded baselines. The gate is the run's exit code; what counts as a regression is [your policy][ki-judge]. No PR comment is described. | **Partial.** The [adapter][ki-lg] records graph, model and tool callbacks, and evaluators can read them. Results group by evaluation, not node. Child nodes are recorded but not served on replay, and [node-boundary replay is listed as deferred][ki-lg13]. | **Partial.** [Listing both sessions' nodes][ki-replay] shows where the paths split, by hand. Nothing labels them, and no cause attribution is claimed. | **Partial.** [Baseline and result means and pass rates][ki-exp], a [held band and a written calibration protocol][ki-judge]. No test or interval found. | **Yes.** [Investigations][ki-inv] store annotations. Evaluators are checked against them, and confirmed sessions become cohorts. | **Unknown.** The guide's `--timeout 1800` is a limit. |
| **Langfuse** experiment-action | **Partial.** The [action][lf-action] comments on the PR, and your script raises `RegressionError` at your threshold. Docs give [code you copy][lf-baseline] to check an approved baseline in your repo. | **Partial.** LangGraph runs are [traced][lf-lg], and [observation-level evaluators][lf-judge] score single operations. The [comment][lf-body] has run scores and item rows. | **No.** The [comment][lf-body] has no node or base comparison. Docs send you to [the failing item's trace][lf-compare]. | **Partial.** [Score Analytics][lf-kappa] gives Cohen's kappa, F1 and agreement, with a [calibration guide][lf-calib]. Neither decides the PR result. | **Yes.** [Annotation queues][lf-queues] and human scores. Reviewed verdicts can become the [approved baseline][lf-baseline]. | **Unknown.** Nothing found. |
| **Braintrust** eval-action | **Yes.** The [action][bt-action] comments per-score improvements and regressions [against a baseline experiment][bt-compare]. Failing on a regression needs [a custom reporter][bt-ci]. | **Partial.** LangGraph logs [one span per node][bt-lg], but the comment is per score and [comparisons stay at trace level][bt-traces]. | **Partial.** [Loop][bt-loop] can diagnose a failing trace. The [comment code][bt-main] only has per-score deltas. | **Partial.** [Trials][bt-trials] measure variance, and the comment shows deltas and counts. No test or interval found. | **Yes.** [Human review][bt-review] with [several reviewers][bt-multi], feeding datasets. | **Unknown.** Nothing found. |
| **promptfoo**-action | **Partial.** Its [README][pf-action] says it evaluates only the current checkout, with no base run. Pass/fail counts and `fail-on-threshold`. | **Partial.** [Trace and trajectory assertions][pf-assert] work when the [provider][pf-lg] emits spans. The comment is aggregate. | **No.** One checkout and aggregate counts leave nothing to attribute. | **Partial.** [`repeat-min-pass`][pf-action] per test, and a [Wilson 95% interval][pf-wilson] on repeat stability, within one version. | **Partial.** The [web viewer][pf-ui] takes pass, fail, score and comment. They reach exports, not a later PR. | **Unknown.** Nothing found. |
| **DeepEval** in CI | **Partial.** `deepeval test run` [fails the build][de-ci] on a metric threshold. Comparing with an [`--official` run][de-official] needs Confident AI, its hosted side. | **Partial.** [Nodes become spans][de-lg], and you [stage a metric on one][de-node]. You wire each, and it's [single-turn only][de-comp]. | **No.** The CLI reports per-metric pass or fail. Nothing found on Confident AI either. | **Partial.** Thresholds and a [flaky marker][de-flaky] in CI. [Eval Alignment][ca-align] on Confident AI reports agreement and a confusion matrix; you decide what gates. | **Yes, hosted.** [Annotation and verdict validation][ca-hitl] on Confident AI. Nothing in the open-source CLI. | **Unknown.** Nothing found. |
| **lgcov** | **Yes.** The [Action][lg-action] replays the PR against a committed cassette and comments. Base isn't re-run on the PR. | **Yes.** The comment has a row per node. A [subgraph counts as one node][lg-gaps], so nodes inside it can't be named. | **Yes, pilot.** The [first node to drift][lg-root] in each run is root, later ones downstream. [Root precision 1 of 1 on one PR][lg-real], 95% Wilson interval 0.21 to 1.00. | **Yes, built.** [Paired test per KPI][lg-gate], and a judge's band follows its [TPR and TNR lower bounds][lg-why]. [Not yet judged on a real PR][lg-kpis]. | **Yes, built.** [`lgcov review override`][lg-why] saves a label in the repo, and the next PR re-estimates the judge. [Labels so far are the demo's][lg-labels]. | **216 s and 538 s** on [one PR][lg-real], capped settings. [Under 1 s on the demo][lg-speed], no real model. |

## Newer tools (one row each)

Read on 11 Nov 2026 from each tool's own page, PyPI or npm entry, and pypistats or npm's
download API; nothing installed or run. Adoption is downloads in the last month where a
registry gives them, else GitHub stars. "Not found" means the pages read are silent. The last
six rows, and the adoption numbers dated 13 Nov, come from a research brief and a follow-up memo
of 13 Nov 2026 that read each tool's README, docs or PyPI page; they were not re-read here.

| Tool | What it does | Verdict | Adoption | Overlap with lgcov |
|---|---|---|---|---|
| [EvalGate][eg-site]¹ | Hosted: reruns agreed examples after each change and posts a regression-gate comment on the PR | Pass or fail per check | `@evalgate/sdk` 1,724 npm (11 Oct to 9 Nov) | PR comment. TPR or TNR gating not found on the pages read |
| [evalgate-ci][eg-ci]¹ | Action: sticky PR comment, Wilson lower bound on the pass rate, McNemar against a baseline, judge kappa, TPR and TNR against human anchors | Exit 0 or 1; "inconclusive" is a label in its sample output, not a third verdict | 27 PyPI (13 Nov), 0 stars | (1), (4) and the paired test of (2) of the [bundle](why-us.md#whats-still-open), with no third verdict; no graph nodes |
| [statgate][sg-pypi] | Paired test against a non-inferiority margin, sticky comment Action | SHIP / BLOCK / INCONCLUSIVE | 17 PyPI (13 Nov) | (1) and (2); lgcov's `stats.py` follows it |
| [llm-evalgate][le-pypi] | Fails CI on a significant regression against a saved baseline; corrects pass rates by the judge's sensitivity and specificity | Pass or fail, with bootstrap intervals | 25 PyPI, 0 stars | (4)-adjacent; no PR comment found |
| [SCIPE][scipe] | An LLM judge on each node of a LangGraph graph; separates a node's own failures from inherited ones and names the node to fix | A ranked node, no gate | 25 stars; last pushed Nov 2024 | Node cause on one version; no base vs PR, no CI |
| [trajectory-causal-attribution][tca] (`agent_replay`) | Ablates recorded trajectory steps one at a time; Shapley values with Wilson intervals | A culprit step | 6 PyPI, 2 stars | Cause by replay, per step not per node; no PR Action |
| graphtrace | pytest plugin: LangGraph cassettes, path and tool asserts, `calibrate` gives kappa | Pass or fail asserts | 160 PyPI (13 Nov), 1 star | Cassettes and kappa; no root labels ([why-us](why-us.md#related-tools)) |
| [agentverify][av-pypi] | pytest plugin: cassettes that fail when a prompt or tool schema changed; LangGraph adapter | Pass or fail, `min_pass_rate` | 60 PyPI, 8 stars | Cassette replay; its README pitches nightly runs, not every PR |
| [evalshift][es-gh] | CLI and Action: compares models, catches tool-call regressions, gates model changes | Not found | 12 stars | CI gate for model swaps; no nodes |
| [Regrada][rg-gh] | Action wrapper for the Regrada CLI, updates a PR comment | Not found | 0 stars | PR comment |
| [Evalship][ev-site] | Hosted: writes and audits evals, compares `main` with the PR in a comment, can block merges; free tier, then $29 and $99 a month | Pass or fail per eval | Not found | PR comment against base |
| [Raindrop Simulations][rd-sim] | Hosted public beta: replays production conversations against the PR's code, side by side | "7 of the 10 conversations" touched, "2 got worse" | Not found; pricing before GA | Base vs PR by conversation; no root cause shown |
| [LangSmith Engine][ls-engine-ov] | Diagnoses a root cause from traces and connected source code and opens the fix as a PR | Per-trace verdict in fix validation (beta) | Not found | Root cause by trace, not per node; see the LangSmith row above |
| [agentgate][ag-gh]² | Go: paired, task-clustered t-test, pass^k, O'Brien-Fleming sequential bounds, Cohen's kappa; its Action's PR comment names the tasks that got worse | PASS / FAIL / INCONCLUSIVE; exit 0 for PASS or INCONCLUSIVE, 1 for FAIL | 0 stars; not on PyPI; last commit 8 Nov | (1) and (2); no graph nodes |
| [evalproof][ep-pypi] | Judge confusion matrix, TPR, FPR and kappa on a gold set; corrected pass rate; exact McNemar, paired bootstrap, Holm across gated metrics; Action | Exit 1 regression; exit 4 "cannot gate" (too few cases, no paired scores, or the dataset or judge changed) | 366 PyPI, 1 star; 0.5.1 on 13 Nov | (2) and (4); verifies a committed run file; no graph nodes |
| [agentstats][as-pypi] | Wilson, exact McNemar, cluster-robust errors, pass@k and pass^k; judge agreement (Cohen, Fleiss, Krippendorff); `init-ci` writes PR, nightly and release workflows | Pass, fail, inconclusive or unhealthy (exit 0 to 3); an under-powered run is inconclusive | 1,553 PyPI, 0 stars; 0.14.0 on 6 Nov | (2) and judge agreement checks; no graph nodes |
| [holdline][hl-pypi] | Action that fails the PR on trajectory checks, pass^k, exact McNemar and a paired bootstrap; `calibrate --strict` against human labels | PASS or FAIL only | 413 PyPI; 0.2.2 on 3 Nov, first release 21 Oct | Paired test and judge calibration; path checks; no third verdict |
| [assay-evals][ae-gh] | Exact McNemar against the base; golden-set judge calibration and `assay rejudge`; Action posts a PR comment; checks come from the base branch by default | Exit 0, 1, or 6 for inconclusive | 445 PyPI; last commit 30 Oct | (1), (2) and (4); the closest found to [a PR can't grade itself](why-us.md#what-lgcov-does-differently) |
| [Plumloom Autoeval][pl-gh] | Hosted evaluation service with a CLI: several judges score each answer, one report comment per suite; its README names no paired test and the method isn't published | PASS / FAIL / INCONCLUSIVE, where INCONCLUSIVE means no threshold is configured, not an under-powered test; exit 1 for both FAIL and INCONCLUSIVE | 534 npm `@plumloom/cli` (14 Oct to 12 Nov), 23 stars | PR comment; no statistical verdict documented |

¹ At least five unrelated projects use the name EvalGate: evalgate.com and, on GitHub,
AOTP-Ventures/evalgate, dylanmccavitt/evalgate, josep-audenis/evalgate and
puneethkotha/evalgate (evalgate-ci on PyPI). The first two rows are the first and last of these.

² athish-shubhan/agentgate, in Go. The PyPI packages `agentgate` and `agentgate-eval` are other
projects.

Left out because one search each found nothing to source: Culprit, maida-assert, checkwash,
evaldelta and agent-tracegraph.

## Where lgcov is different (evidence-backed)

Against these seven, as documented. Each has a limit next to it.

- **Root vs propagated per node, between base and PR.** None of the seven labels graph nodes
  this way. The nearest are Kitaru's node listings, which you read by hand, and root-cause
  diagnosis of a failing trace in LangSmith Engine, Opik's Ollie and Braintrust's Loop.
  lgcov's evidence is a [pilot][lg-real], not proof: one PR, and 1 of 1 roots. Recall is
  about 1/5 to 1/6 with first-drift roots, none with the multi-root rule (T162) on its saved runs, and
  a subgraph is scored as one node. A noise check, base against base on a real model (T173),
  gave the same root as the PR, so these runs can't separate cause from model noise: lgcov's
  root has no noise threshold in the gate yet (`lgcov bench --noise-repeats` has one, T211,
  measured on a seeded-noise fixture only). Among the newer tools, several try node cause; none found
  ships it replay-proof in a sticky PR Action. [SCIPE][scipe] separates a node's own failures
  from inherited ones with an LLM judge, on one version, and
  [trajectory-causal-attribution][tca] finds a culprit step by replay, not a node. A plain
  baseline matches or beats lgcov on single-node edits: naming the node the diff edited has
  recall 1.00 against lgcov's 0.85 and precision 0.35 against 0.39
  ([synthetic benchmark](benchmark.md#synthetic)). A router edit now names its routing node
  as root (T175), which lifts lgcov's recall on all synthetic edits from 0.55 to 0.90, still
  under the diff's 1.00. lgcov doesn't check that the route changed, so a router edit beside a
  node edit can be named for drift it didn't cause.
- **A judge earns the right to block.** lgcov lets a judge block only when the lower bounds of
  its TPR and TNR against human labels clear 0.80. Langfuse reports kappa and F1, Confident AI
  a confusion matrix and Kitaru a calibration protocol, and LangSmith and Opik tune judges from
  corrections, but none documents a gate that reads such bounds. Among the newer tools,
  checking a judge against human labels is table stakes, not a differentiator:
  [evalgate-ci][eg-ci] reports TPR and TNR and blocks on the judge's kappa,
  [llm-evalgate][le-pypi] uses sensitivity and specificity, and [evalproof][ep-pypi],
  [agentstats][as-pypi], [assay-evals][ae-gh] and [holdline][hl-pypi] calibrate against labels
  too. evalgate-ci and llm-evalgate don't gate on interval bounds; the sources for the other
  four don't say. This is built, but it hasn't
  judged a real PR: `lgcov judge --from-runs` can judge saved runs without replaying the graph,
  but no real PR's runs have been judged yet, and the Jev judge is untested at this payload size.
- **A paired test per KPI in the PR result.** None of the seven documents a test or interval
  between base and PR. Braintrust and Kitaru show deltas, and promptfoo's Wilson interval is
  within one version. Among the newer tools it is table stakes, not a differentiator:
  [statgate][sg-pypi], [agentgate][ag-gh], [agentstats][as-pypi], [evalproof][ep-pypi] and
  [assay-evals][ae-gh] each ship a paired test with a verdict that can say it cannot decide,
  and [AgentAssay](why-us.md#related-tools) gives three-valued verdicts. On the one real PR, with one case and one run per side, the results
  [aren't gate verdicts][lg-kpis].
- **Serving by node.** lgcov serves an unchanged node from the cassette, by its code fingerprint
  and masked input, so a change only to a masked value, such as a message's id or usage, can be
  served stale ([known limits](../README.md#known-replay-limits)). LangSmith's [pytest cache][ls-cache]
  replays HTTP calls. Kitaru re-runs the agent
  [from the top][ki-concept13], and on LangGraph it answers tool calls from recordings only for
  [factory-built agents][ki-lg]. It serves tool results only: model calls run live, and its
  adapter lists [node-boundary replay as deferred][ki-lg13], so this could change. Limits: a node holding a compiled subgraph always runs live,
  so a live model's new answer can read as drift. A comment or docstring edit currently changes
  a node's fingerprint, so that node runs live. `Document`s and custom pydantic objects inside
  state fields can't be saved yet. Speed is [measured only on the demo][lg-speed].
- **Labels in the repo, no server.** A reviewer's override is a label in git, read back on the
  next PR. LangSmith, Opik, Langfuse, Braintrust and Confident AI keep review data on a platform,
  though Opik and Langfuse can be self-hosted, and Kitaru needs [its own server and
  workers][ki-deploy]. Others keep things in the repo too: Langfuse's approved baseline, and
  the label or anchor files of [assay-evals][ae-gh], graphtrace, [holdline][hl-pypi] and
  [evalgate-ci][eg-ci]. The loop as one piece (override a verdict, save the label and a
  regression case, move the band, all through git) was not found as a whole in the tools read
  on 13 Nov 2026; LangSmith, Braintrust, Confident AI and LangWatch were not checked for it.
  The labels so far are the demo's.
- **A PR can't grade itself.** On a PR, lgcov reads the gate's policy from the base commit,
  binds each result to the base and head SHAs, and can run the PR's code in a
  [separate job](trust-boundary.md) from the one that decides the verdict. That combination
  was not found as a whole in the tools read on 13 Nov 2026. [assay-evals][ae-gh] is the
  closest: its checks come from the base branch by default, in one job and with no result
  bound to the SHAs. LangSmith, Braintrust, Confident AI and LangWatch were not checked.
  Limits: one job is still the default, and a forged `candidate.json` or an edited workflow
  file can still mislead it ([what is not covered](trust-boundary.md#what-is-not-covered)).

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
- 13 Nov 2026: Kitaru's [replay][ki-concept13] and [LangGraph adapter][ki-lg13] docs were re-read
  at commit `1501489`; its other links stay at the commit read on 5 Nov. Its six scores were
  re-checked against that and didn't change. The six newest rows link a PyPI version or a
  repo with the date read, not a pinned commit.
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
[ki-concept13]: https://github.com/zenml-io/kitaru/blob/1501489c92b4fe34dc644e0b653684f66aff13c5/docs/book/concepts/replay.md
[ki-lg13]: https://github.com/zenml-io/kitaru/blob/1501489c92b4fe34dc644e0b653684f66aff13c5/docs/book/adapters/langgraph.md
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
[eg-site]: https://evalgate.com
[eg-ci]: https://pypi.org/project/evalgate-ci/
[sg-pypi]: https://pypi.org/project/statgate/
[le-pypi]: https://pypi.org/project/llm-evalgate/
[scipe]: https://github.com/garg-ankush/scipe
[tca]: https://pypi.org/project/trajectory-causal-attribution/
[av-pypi]: https://pypi.org/project/agentverify/
[es-gh]: https://github.com/evalshift/evalshift-cli
[rg-gh]: https://github.com/regrada-ai/regrada-ci
[ev-site]: https://evalship.com/
[rd-sim]: https://www.raindrop.ai/simulate/
[ls-engine-ov]: https://docs.langchain.com/langsmith/engine-overview
[ag-gh]: https://github.com/athish-shubhan/agentgate
[ep-pypi]: https://pypi.org/project/evalproof/0.5.1/
[as-pypi]: https://pypi.org/project/agentstats/0.14.0/
[hl-pypi]: https://pypi.org/project/holdline/0.2.2/
[ae-gh]: https://github.com/tap222/assay-evals
[pl-gh]: https://github.com/AlchemyInCode/plumloom-autoeval-oss
[lg-action]: ../action.yml
[lg-root]: https://github.com/AMOHYZ/langgraph-regression-guard/blob/f5b313c8de32e415770cd78804bbe3865ec40915/src/lgcov/cli.py#L286-L307
[lg-gaps]: benchmark.md#known-gaps
[lg-real]: benchmark.md#real-prs
[lg-kpis]: benchmark.md#agent-kpis
[lg-gate]: gate.md
[lg-why]: why-us.md#what-lgcov-does-differently
[lg-labels]: ../calibration/labels/demo.csv
[lg-speed]: benchmark-speed.md
