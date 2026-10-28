# langgraph-regression-guard

Catch silent regressions in LangGraph agents on every PR: cassette replay of
node I/O, calibrated LLM judges, and human review, as a GitHub Action.

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

- **L1 — Cassettes.** Record each node's inputs and outputs once, then replay
  them on every PR. The diff tells you whether a change breaks a node at its
  root or only propagates from upstream.
- **L2 — Judge gate.** Typed, calibrated LLM judges score KPIs with explicit
  probabilities and a confidence gate: low-confidence verdicts defer to a
  human instead of guessing.
- **L3 — Review loop.** An accept / reject / override decision per verdict;
  accepting re-baselines the cassette, overriding recalibrates the judge.

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
```

Precedence: CLI flag > file > default.

## Development

```bash
cp .env.example .env   # fill in JEV_API_URL / JEV_API_KEY for L2/L3
uv sync
uv run pytest          # offline: fake judge, no network calls
uv run graph-diff old.json new.json   # node/edge diff of two graph snapshots
```

## Roadmap

- Calibrated typed judges: verified probability calibration per KPI.
- Self-improvement loop: reviewer overrides feed back into judge calibration.
- Ship as a GitHub Action that posts the regression report on every PR.

## License

MIT — see [LICENSE](LICENSE).
