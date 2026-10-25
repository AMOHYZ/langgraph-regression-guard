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
