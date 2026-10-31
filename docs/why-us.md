# Why lgcov

Five reasons to pick lgcov over [Reenact](https://github.com/Manas-33/Reenact) or
[EvalView](https://github.com/hidai25/eval-view), each followed by where it stands
today. Compared against reenact 0.2.0 and evalview 0.8.1 on 2026-10-31.

**(a) Root vs propagated drift, per node.** When a PR changes an agent, lgcov names the
node that drifted on its own input (root) and separates it from the nodes that only
drifted because something upstream did (propagated): in the
[PR #163 taxonomy](taxonomy.yaml), a rewritten supervisor prompt is root drift, and the
unsupported claims it causes in `final_report` are propagated drift.
*Status: shipped.* `diff_cassettes` classifies root and propagated drift from recorded
node inputs, and `lgcov replay` marks the first node to drift in each run as root.
Reenact reports where a run diverges from its recording. EvalView attributes a
regression to a root tool call (added, removed, reordered or with changed parameters),
deterministically. Neither says which graph nodes only drifted because of an upstream
node.

**(b) Seconds per PR, not minutes.** Nodes a PR didn't change are served from recorded
cassettes instead of the model, and typed judges return a verdict in 0.15–0.44 s per
Jev call.
*Status: not built yet.* `lgcov replay` re-runs every node live and compares the result
with the cassette, which is fast only because the demo agent's LLM is fake. Serving
unchanged nodes from cassettes is still to do, and the Jev latency figure has no
benchmark in this repo yet. Reenact already serves model responses from its recordings,
so once built this matches Reenact rather than beating it.

**(c) Judges you can trust.** Every judge reports Cohen's kappa with a confidence
interval, plus its TPR and TNR against human labels and how many labels that interval
needs.
*Status: planned.* Reenact already measures each criterion's raw agreement with human
labels, next to human-vs-human agreement, and uses it to make the criterion blocking or
advisory. The planned edge over it is chance-corrected kappa with an interval, and
TPR/TNR.

**(d) Significance, not thresholds.** Each KPI compares base and PR scores with a paired
test and reports a p-value and an effect size, where Reenact gates on a fixed score
tolerance (0.05) and EvalView on fixed score thresholds, or in its statistical mode on a
fixed pass rate (by default 80% of 10 runs).
*Status: shipped (T091).* Each case's base and PR scores are paired (exact McNemar, or a
sign-flip test), with Δ, its 95% CI, a Holm-adjusted p-value, the MDE and the cases still
needed (N*), and a non-inferiority margin decides SHIP, BLOCK or INCONCLUSIVE. EvalView's
statistical mode does report confidence intervals, for one version's pass rate.

**(e) Human override recalibrates the judges.** When a reviewer overrides a verdict, that
label feeds back into the judge for that KPI.
*Status: half built.* An override appends the labelled example to a calibration file
(`review.apply_decision`), but nothing reads that file back into a judge yet. Reenact
already uses human labels to set each criterion's gate level.

## Where that leaves us

(a) and (d) are ahead today. (b), (c) and (e) are still to
build, and Reenact already ships simpler versions of all three.

## How this was checked

- Competitors: the reenact 0.2.0 and evalview 0.8.1 wheels from PyPI, read but not run.
  Reenact: `evals/baseline.py` (`DEFAULT_SCORE_TOLERANCE = 0.05`) and
  `evals/calibration.py` (agreement with human labels sets blocking or advisory).
  EvalView: `core/types.py` (`VarianceConfig`: 10 runs, 0.8 pass rate, 0.95 confidence
  level) and `core/root_cause.py` (deterministic root-tool attribution, optional AI
  enrichment).
- lgcov: `cassette.py` (`replay`, `diff_cassettes`), `stats.py` (`paired_test`,
  `holm`) and `review.py` (`apply_decision`).
- Not yet compared live: Reenact records LangChain chat-model and tool calls through
  callbacks, and the demo agent's fake LLM is plain Python, so Reenact would record
  nothing on demo PR #7. A fair run needs the demo to call a LangChain chat model.
