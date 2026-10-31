# langgraph-regression-guard

Catch silent regressions in LangGraph agents on every PR: cassette replay of
node I/O, LLM judges with a confidence gate, and human review, as a GitHub Action.

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

How this compares with Reenact and EvalView, and what is built today:
[docs/why-us.md](docs/why-us.md).

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
  min_node_coverage: 80            # percent, 0..100
  max_drifted_nodes: 3             # optional; omit for no limit
  fail_on: [fail]
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
  baseline yet". A missing or unreadable baseline is a warning, never a failure.
- Unchanged nodes are served from the cassette; `live: true` runs every node live.
- A node whose code is unchanged but whose input no longer matches the recording has
  diverged. `on-divergence: flag` (the default) runs it live and marks what ran after
  it, `live` runs the rest of that run live, and `fail` leaves the node and the rest of
  the run not checked. `budget-seconds` stops starting live node calls once the run is
  projected to take longer, and the nodes it stops before are not checked either. A node
  not checked is INCONCLUSIVE: never a fail, never a harness error.
- The report ends with where the time went, e.g. `⏱ 42 s · agent 31 s (3 live / 1 served)
  · judges 9 s (12 calls, 1.4k tokens) · budget 90 s`.
- With `taxonomy:`, the run also gates the taxonomy's KPIs on the replayed outputs (see
  [The gate](#the-gate-ship-block-or-inconclusive)) and adds the table to the report, at
  most 200 model judge calls per run. `judge: jev` KPIs read `JEV_API_URL` and
  `JEV_API_KEY`: set them in the job's `env:` from secrets.
- Outcomes and exit codes are in the table below.
- Inputs, all optional and falling back to `lgcov.yaml`: `graph-entrypoint`,
  `cassette-dir`, `taxonomy` (turns on the KPI gate; `taxonomy_path` isn't read here),
  `fail-on`, `fail-on-inconclusive`, `on-divergence`, `budget-seconds`, `python-version`,
  `github-token`, `live`. Outputs: `report-path`, `root-drift-count`,
  `propagated-drift-count`, `verdict`.
- Runs on Linux and macOS runners.

### Outcomes

| Outcome | Exit | When |
|---|---|---|
| PASS / SHIP | 0 | No drift that `fail-on` cares about, and every blocking KPI is SHIP. |
| AGENT_FAIL / BLOCK | 1 | Drift that `fail-on` cares about, a blocking KPI is BLOCK, or a node in the PR's graph raised; the report names the node. A node that raises is AGENT_FAIL even with `fail-on: never`: `fail-on` governs drift only. |
| INCONCLUSIVE | 0, or 1 with the switch | A blocking KPI is neither SHIP nor BLOCK (shown as ⚠️ `needs_human`, with the cases it still needs), or replay left nodes not checked: a divergence with `on-divergence: fail`, or a budget hit. `fail-on-inconclusive: true` (or `gate.inconclusive_blocks: true`) makes it exit 1, never 2. |
| INFRA_ERROR | 2 | Install, graph import, a missing or broken cassette, network trouble (`TimeoutError`, `ConnectionError`, `httpx.TransportError`) in a judge or a node, or a blocking KPI left with no valid judgment on a side. Reported as "⚠️ Harness error, not a regression", naming the node when it came from one. |

A regression found in the same run wins over a judge's infra error: exit 1.

Known limit: an exception in a router (a conditional-edge function) or inside a
subgraph isn't attributed to a node yet, so it counts as INFRA_ERROR (T109).

`lgcov selfcheck` checks this classification. It runs the harness on fixtures whose
outcome is known (`tests/fixtures/selfcheck/`: a real refactor that must PASS, a prompt
change and a crashing node that must be AGENT_FAIL with the right root, and a missing
cassette that must be INFRA_ERROR), prints expected vs actual, and exits 1 on any
mismatch. CI runs it as its own job on every push.

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
```

Planned: sequential stopping (mSPRT), to stop judging once a KPI's verdict is clear.

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
