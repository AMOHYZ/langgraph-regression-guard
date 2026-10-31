# langgraph-regression-guard

Catch silent regressions in LangGraph agents on every PR. lgcov tells you which node a
PR actually changed and which ones only inherited the damage, gates each KPI in your
taxonomy with a paired test, and lets a judge block only once it has earned that against
human labels. A GitHub Action; first report in 2 minutes.

[![CI](https://github.com/AMOHYZ/langgraph-regression-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/AMOHYZ/langgraph-regression-guard/actions/workflows/ci.yml)

## The problem

In a multi-node LangGraph agent, one PR that changes a single prompt can
degrade every node downstream of it — silently.
[open_deep_research#163](https://github.com/langchain-ai/open_deep_research/pull/163)
rewrote a supervisor system prompt; CI stayed green because nothing exercised
the changed nodes' behaviour. Node-level tests can't catch prompt drift: the
graph still compiles, the edges still fire, the output just gets worse.

## The solution

Three layers, one per level of confidence:

- **L1 — Cassettes.** Record each node's input and output once. On every PR,
  nodes the change left alone are served from the recording and the rest re-run
  and are compared with it. The diff tells you whether a change breaks a node at
  its root or only propagates from upstream.
- **L2 — Judge gate.** Typed LLM judges and deterministic code checks score
  KPIs, each verdict with a confidence, and a paired test per KPI decides SHIP,
  BLOCK or INCONCLUSIVE: an unclear or low-confidence result goes to a human
  instead of guessing.
- **L3 — Review loop.** An accept / reject / override decision per verdict;
  accepting re-baselines the cassette, and an override is saved as a labelled
  example for calibrating the judge.

How this compares with Reenact, graphtrace, EvalView, AgentAssay, statgate, Kitaru and
LangSmith, and what is built today: [docs/why-us.md](docs/why-us.md).

## Quickstart

Needs Python 3.12+. Runs offline: the demo agent uses a fake, deterministic LLM.

Minutes to first report: 2 (clean venv, steps below pasted as-is; measured 2026-10-27,
41–70 s of which is `pip install`).

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install "git+https://github.com/AMOHYZ/langgraph-regression-guard"
lgcov --version

# 1. A graph to guard: the demo agent (or point graph_entrypoint at your own).
curl -fsSLO https://raw.githubusercontent.com/AMOHYZ/langgraph-regression-guard/main/examples/demo_agent/graph.py
printf 'graph_entrypoint: graph.py:graph\ncassette_dir: cassettes\n' > lgcov.yaml

# 2. Record a baseline: one cassette run per input.
echo '[{"question": "Where is my order?"}, {"question": "What are your hours?"}]' > inputs.json
lgcov record --inputs inputs.json

# 3. Replay it: the first report.
lgcov replay                 # "No cassette drift (2 runs)."

# 4. Make a change and replay again.
sed -i.bak 's/a concise support/a friendly, detailed support/' graph.py
lgcov replay                 # `respond` | root | 2/2, exit code 1

# 5. The change is intended: re-baseline only that node.
lgcov cassette accept --nodes respond
lgcov replay                 # "No cassette drift (2 runs)."
```

Example `lgcov.yaml`:

```yaml
graph_entrypoint: graph.py:graph   # required, "<file.py>:<attr>"
cassette_dir: cassettes
taxonomy_path: taxonomy.yaml       # optional; `taxonomy:` is an alias
thresholds:
  min_node_coverage: 80            # percent; under it `lgcov ci` exits 1 (default 0: off)
ignore_paths: [/meta/nonce]        # JSON pointers into node I/O
gate:                              # the L2 KPI gate; see "The gate" below
  margin: 0.05                     # δ; a KPI's own `margin:` in taxonomy.yaml wins
```

Precedence: CLI flag > file > default.

### Which nodes replay re-runs

`lgcov replay` and `lgcov ci` (the Action) treat a node as unchanged, and serve
its recorded output without running it, when two things match the recording:

- **Its input**, with volatile fields and `ignore_paths` masked as in the
  comparison. A node downstream of a change gets a new input, so it re-runs.
- **Its code fingerprint**: the node function's source, defaults and closure
  values, plus the values of the module-level names it uses, following the
  functions, classes and modules of your project the same way. Library code
  counts by name, and other objects (a chat model client, say) by their repr.

Everything else runs live: changed nodes, nodes that are not plain functions
(subgraphs, `ToolNode`) and every node of an older cassette with no recorded
inputs. What a node reads at run time (files, environment variables, network) and
dependency upgrades are not part of the fingerprint: pass `--live` to either
command to re-run every node.

Served vs `--live` on the demo, measured: [docs/benchmark-speed.md](docs/benchmark-speed.md) (`make bench-speed`).

Root vs propagated attribution, measured: `lgcov bench DIR` records each benchmark's cases
on base, replays head, and scores lgcov's labels against hand-written ones, precision /
recall / F1, micro and macro ([docs/benchmark.md](docs/benchmark.md)). For now its rows are
three synthetic fixtures in `tests/fixtures/bench/`; real PRs come with T100. A miss on a
hard case (Send fan-out, subgraph, loop) is listed as a known gap, never hidden.

## Use in your repo

Record a baseline first (see Quickstart), commit `lgcov.yaml` and your cassettes,
then add this workflow:

```yaml
name: lgcov
on:
  # Not pull_request_target: the Action runs your PR's own graph code.
  pull_request:
  push:
    branches: [main]  # main's runs record the coverage baseline PRs compare against
permissions:
  contents: read
  pull-requests: write
  actions: read  # to download main's coverage baseline
jobs:
  lgcov:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: AMOHYZ/langgraph-regression-guard@v0
        with:
          fail-on: root-drift  # root-drift | any-drift | never
```

- Each PR gets one report comment, updated in place on every push. PRs from forks get
  a read-only token, so there the report goes to the job summary instead.
- `root-drift` and `any-drift` fail the same PRs for now: until replay classifies
  drift by the node inputs it records, the first node to drift in each run counts
  as root.
- The report includes coverage of the replayed runs, e.g. `Coverage: Nodes 75% (−25% vs
  main) · Edges 60% (±0)`, and lists nodes main reached that this PR doesn't. Pushes to
  `main` upload it as the `lgcov-coverage` artifact; until one exists, PRs show "no
  baseline yet". A missing or unreadable baseline is a warning, never a failure. Node
  coverage under `thresholds.min_node_coverage` fails the run (exit 1), and the report
  lists the nodes no run reached.
- Unchanged nodes are served from the cassette; `live: true` runs every node live.
- A node whose code is unchanged but whose input no longer matches the recording has
  diverged. `on-divergence: flag` (the default) runs it live and marks what ran after
  it, `live` runs the rest of that run live, and `fail` leaves the node and the rest of
  the run not checked. `budget-seconds` stops starting live node calls once the run is
  projected to take longer, and the nodes it stops before are not checked either. A node
  not checked is INCONCLUSIVE: never a fail, never a harness error.
- The report ends with where the time went, e.g. `⏱ 42 s · agent 31 s (3 live / 1 served)
  · judges 9 s (12 calls, 1.4k tokens) · budget 90 s`.
- Two tiers. `pr` (the default) serves unchanged nodes, judges each case once, skips
  `judge: llm` KPIs (shown as "nightly only") and has a 90 s budget. `nightly` runs every
  node live with every judge, k from `samples_per_case` (3 if unset), no budget, and
  uploads its report as the `lgcov-nightly` artifact, which PRs link. Run it on a schedule:

  ```yaml
  on:
    schedule:
      - cron: "0 3 * * *"  # the nightly tier, on main
  # ...the same job as above, with:
        - uses: AMOHYZ/langgraph-regression-guard@v0
          with:
            tier: ${{ github.event_name == 'schedule' && 'nightly' || 'pr' }}
  ```
- With `taxonomy:`, the run also gates the taxonomy's KPIs on the replayed outputs (see
  [The gate](#the-gate-ship-block-or-inconclusive)) and adds the table to the report, at
  most 200 model judge calls per run. `judge: jev` KPIs read `JEV_API_URL` and
  `JEV_API_KEY`: set them in the job's `env:` from secrets.
- Outcomes and exit codes are in the table below.
- Inputs, all optional and falling back to `lgcov.yaml`: `graph-entrypoint`,
  `cassette-dir`, `taxonomy` (turns on the KPI gate; `taxonomy_path` isn't read here),
  `fail-on`, `fail-on-inconclusive`, `on-divergence`, `budget-seconds`, `tier`,
  `python-version`, `github-token`, `live`. Outputs: `report-path`, `root-drift-count`,
  `propagated-drift-count`, `verdict`.
- Runs on Linux and macOS runners.

### Outcomes

| Outcome | Exit | When |
|---|---|---|
| PASS / SHIP | 0 | No drift that `fail-on` cares about, and every blocking KPI is SHIP. |
| AGENT_FAIL / BLOCK | 1 | Drift that `fail-on` cares about, a blocking KPI is BLOCK, node coverage under `thresholds.min_node_coverage`, or a node in the PR's graph raised; the report names the node (a router by its function name, a node inside subgraph `sub` as `sub.inner`). A node that raises is AGENT_FAIL even with `fail-on: never`: `fail-on` governs drift only. |
| INCONCLUSIVE | 0, or 1 with the switch | A blocking KPI is neither SHIP nor BLOCK (shown as ⚠️ `needs_human`, with the cases it still needs), or replay left nodes not checked: a divergence with `on-divergence: fail`, or a budget hit. `fail-on-inconclusive: true` (or `gate.inconclusive_blocks: true`) makes it exit 1, never 2. |
| INFRA_ERROR | 2 | Install, graph import, a missing or broken cassette, network trouble (`TimeoutError`, `ConnectionError`, `httpx.TransportError`) in a judge or a node, or a blocking KPI left with no valid judgment on a side. Reported as "⚠️ Harness error, not a regression", naming the node when it came from one. |

A regression found in the same run wins over a judge's infra error: exit 1.

`lgcov selfcheck` checks this classification. It runs the harness on fixtures whose
outcome is known (`tests/fixtures/selfcheck/`: a real refactor that must PASS; a prompt
change, a crashing node, a crashing router and a crashing node inside a subgraph that
must be AGENT_FAIL with the right root; and a missing cassette that must be
INFRA_ERROR), prints expected vs actual, and exits 1 on any mismatch. CI runs it as its
own job on every push.

## Judge KPIs locally (L2)

`lgcov judge` runs a taxonomy's judges k times per case, on the recorded outputs and on a
fresh replay, and gates each KPI with a paired test (below):

```bash
lgcov judge --taxonomy examples/demo_agent/taxonomy.yaml --samples 3
```

- pass@k: at least one of a case's k samples passes; pass^k: all of them do. A case is
  flaky when it's pass@k but not pass^k. Flakiness is shown as ⚠️ and never changes a
  verdict; an infra error doesn't count toward k and is never a fail.
- It prints the planned number of judge calls (2·n·k per KPI) first, and refuses to
  make more than `--max-calls` (default 200) calls to model judges.
- Cost per KPI: ms and tokens per judgment, and totals. Tokens come from the judge's
  `usage` (`input_tokens`/`output_tokens` or `prompt_tokens`/`completion_tokens`);
  code judges cost 0, and "—" means the judge reported none.
- `samples_per_case` in `lgcov.yaml` sets k; `--samples` overrides it. The demo's
  `fake` judge has a seeded flaky mode, so its table shows a real ⚠️ row.

### The gate: SHIP, BLOCK or INCONCLUSIVE

Each case is judged on base and on the PR, so its two scores pair up: d = PR − base, with
a case's k samples averaged first. Per KPI, against a non-inferiority margin δ:

- **BLOCK** (exit 1): the whole 95% CI of Δ is below 0, Δ ≤ −δ, and the adjusted p is
  under α. A BLOCK the judge isn't confident of (mean confidence under 0.7) is
  INCONCLUSIVE instead.
- **SHIP** (exit 0): the CI's low end is above −δ, so any drop is smaller than δ.
- **INCONCLUSIVE** (exit 0, or 1 with the switch): neither. A ⚠️ row says how many more
  cases it needs: N* is the number needed to detect Δ at α and the set power (Kotawala
  2026, Eq. 6, for paired binary outcomes).

How it's computed:

- A binary KPI with k = 1 gets an exact McNemar test; graded scores or k > 1 get a paired
  sign-flip test (exact up to 20 cases). The CI is a seeded BCa bootstrap; MDE is the
  smallest drop this many cases detects.
- p is adjusted with Holm across the blocking KPIs and Benjamini–Hochberg across the
  advisory ones, so adding KPIs doesn't add false alarms. Advisory KPIs never fail a PR.
- The old outcome names stay as aliases: `pass` = SHIP, `fail` = BLOCK, `needs_human` =
  INCONCLUSIVE; `infra_error` stays its own path.

```yaml
gate:                          # lgcov.yaml; these are the defaults
  alpha: 0.05
  power: 0.8
  margin: 0.05                 # δ; a KPI's own `margin:` in taxonomy.yaml wins
  advisory_kpis: []            # or blocking_kpis: [...]; by default every KPI blocks
  inconclusive_blocks: false   # true: INCONCLUSIVE exits 1, like --fail-on-inconclusive
  uncalibrated: advisory       # a model judge with no labels; or flag (T063)
```

Per node and slice. The report never gives only a mean: after the KPI table it has one table
per node, a row per slice (the KPIs' failure mode) and the node's total when it has more than
one, each pooling its blocking judges' paired cases into one test. The overall verdict is the
worst group's. A group is thin, "⚠️ thin (n=6, need 93)", when it has fewer cases than it needs
to detect a drop of its margin at power 0.8, and a thin group can't block on its own: its BLOCK
is INCONCLUSIVE. A KPI's own BLOCK still blocks. `report.group_by: [node, slice]` in
`lgcov.yaml` chooses the grouping; `[]` turns it off. Graph-structural coverage criteria are
future work.

Planned: sequential stopping (mSPRT), to stop judging once a KPI's verdict is clear.

### Calibrate judges against human labels

`lgcov calibrate` compares each judge with human labels: every CSV under
`calibration/labels/` (columns `case_id, judge_id, judge_label, human_label`, plus optional
`judge_confidence` and `question_form`) and the overrides in `calibration/overrides.jsonl`.
Name CSVs to read those instead; `--taxonomy` gives each KPI's labels (default `pass` /
`fail`).

```bash
lgcov calibrate   # the demo: 40 synthetic labels in calibration/labels/demo.csv
```

- A failure is the positive class. TPR: the share of human-failed cases the judge catches;
  TNR: the share of human-passed cases it clears. Both with Wilson 95% intervals.
- Cohen's κ with a seeded bootstrap interval, and the labels needed for κ ±0.10.
- With confidences: the Brier score, accuracy at each confidence the judge actually gives
  (the pile-up at 1.00 on its own), one temperature per question form (never pooled), and
  from 50 labels on, the P(fail) cutoff with the best cross-validated balanced accuracy.
- An override counts as a label only when it names its KPI and the judge's label; older
  overrides are skipped and counted.

It also writes each judge's band to `calibration/bands.json`, kept in the repo so a band
moves in a reviewed diff. `lgcov judge` and `lgcov ci` read it (`--bands` to point
elsewhere):

| band | when | what the judge can do to a PR |
|---|---|---|
| blocking | the lower bounds of the TPR and TNR intervals both reach 0.80 | BLOCK it |
| flag | both point rates reach 0.80, a lower bound doesn't | stop it as INCONCLUSIVE at most |
| advisory | anything less, or no labels | show ⚠️ rows, never fail it |

- Code judges always block: they are deterministic.
- A model judge with no labels is advisory (`gate.uncalibrated: flag` makes it a flag), and
  the report says so at the top: "⚠️ 1 of 2 judges uncalibrated: no model judge can block
  this PR. Run `lgcov calibrate`."
- Blocking takes at least 16 human-failed and 16 human-passed cases with no disagreement:
  at 16/16 the Wilson lower bound is 0.806. In practice, start as Hamel Husain recommends,
  with about 100 diverse traces ([AI Evals FAQ](https://hamel.dev/blog/posts/evals-faq/)).
- `jev_model:` in `lgcov.yaml` pins the model Jev judges call (default `jev-latest`), and
  bands.json records the one the labels came from. When they differ, the Jev judges' bands
  drop to flag, with a warning, until `lgcov calibrate` runs again.
- The demo's 40 synthetic labels land on flag on purpose: 18 of 20 failures caught, 19 of 20
  passes cleared, an overconfident question form, and 107 labels needed for κ ±0.10.

#### Overrides move bands, through git

1. A PR's report shows a verdict you disagree with, say `answer_states_order_status` passing
   `run-0/respond`.
2. Override it:

   ```bash
   lgcov review override --kpi answer_states_order_status --case-id run-0/respond \
     --judge-label pass --human-label fail --reviewer you --note "drops the order status"
   ```

   The override is saved to `calibration/overrides.jsonl` as a label; when you disagree with
   the judge, the case goes to `calibration/cases/<kpi>.jsonl` too; and the bands are
   recalculated, printing any move: "Judge `answer_states_order_status`: flag → advisory
   (n 42 → 43, lower TPR 0.61 → 0.58)."
3. Commit `calibration/` with the PR. Its report lists the band changes against main under
   "Judge calibration". Nothing is committed for you.
4. The next PR gates with the new bands, and every PR re-judges the saved cases: "the judge
   agrees with the human on 1 of 3 saved cases", with ⚠️ "judge drifting from human labels"
   when that falls under half of at least 4. Saved cases check the judge; they never change
   the gate.

- A band moves up only once the judge has 16 agreed failures and 16 agreed passes; it moves
  down at once.
- One label per case and reviewer: overriding the same case again replaces your earlier
  override, while a second reviewer's counts on its own.

## Development

```bash
cp .env.example .env   # fill in JEV_API_URL / JEV_API_KEY for L2/L3
uv sync
uv run pytest          # offline: fake judge, no network calls
uv run graph-diff old.json new.json   # node/edge diff of two graph snapshots
```

## Roadmap

- Judges you can trust: Cohen's kappa with a confidence interval, and TPR/TNR
  against human labels, per KPI.
- Sequential stopping (mSPRT) for the KPI gate.
- Self-improvement loop: reviewer overrides feed back into judge calibration.

## License

MIT — see [LICENSE](LICENSE).
