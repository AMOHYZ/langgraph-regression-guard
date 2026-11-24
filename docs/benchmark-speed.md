# Speed benchmark

Measured 2026-10-31 on macOS-26.7.1 (x86_64), Python 3.12.4, lgcov 0.1.0: 5 served, 5 --live runs of `lgcov ci` on the demo PR (PR #7's prompt change, which makes `respond` drift), alternating, after one warm-up run that isn't counted. Median (min–max):

| mode | wall-clock | agent | judges | live / served nodes | judge calls | tokens |
|---|---|---|---|---|---|---|
| served | 591.3 ms (583.1 ms–606.0 ms) | 3.3 ms (3.2 ms–3.5 ms) | 0.3 ms (0.3 ms–0.3 ms) | 2 / 3 | 8 | — |
| --live | 594.0 ms (585.2 ms–622.8 ms) | 3.4 ms (3.4 ms–3.9 ms) | 0.3 ms (0.3 ms–0.3 ms) | 5 / 0 | 8 | — |

- served: nodes whose code and input match the cassette return the recorded output; `--live`: every node runs.
- wall-clock is measured around each `lgcov ci` process, so it includes Python start-up and imports. agent (replaying the runs) and judges (the KPI gate) come from the run's own timing (`--timing-out`).
- The demo's LLM is a deterministic fake that answers at once, and its judges are a code check and an offline fake, which report no tokens. So these numbers measure lgcov itself, not model calls.

Reproduce:

```bash
make bench-speed
```

## open_deep_research #163 — served vs `--live` (T194 runbook)

**Not measured in this ladder.** Do not run from an agent session. Estimates below reuse the
pilot Cost / Time rows in [benchmark.md](benchmark.md#real-prs). Caps match that pilot:
clarification off, 1 research unit, 2 researcher iterations, 3 tool calls per researcher;
`gpt-6-luna` also used 32k where applicable.

| mode | expected wall (estimate) | expected spend (estimate) | basis |
|---|---|---|---|
| served (default cassette serve for unchanged fingerprints) | Often close to live wall on this graph: changed model nodes still re-run live when fingerprints move | Lower tool/search cost than full live if upstream nodes serve; still pays for every live model node | #163 rewrote supervisor / researcher prompts — several roots live |
| `--live` (every node live both sides) | ~216 s both sides (`gpt-4.1`) or ~538 s (`gpt-6-luna`) | ~$0.52 (`gpt-4.1`) or ~$0.16 (`gpt-6-luna`) | Recorded pilot rows in benchmark.md |

### How to time it (maintainer only; paid)

```bash
# Clone open_deep_research at the locked SHAs, install its deps, set model keys.
# Case file: docs/benchmark/open_deep_research-163.yaml (EU AI Act question only).
# Prefer --from-runs / keep-runs over a fresh live pair when you only need attribution.

# Served path (unchanged nodes from cassette; changed fingerprints still live):
uv run lgcov bench docs/benchmark --only open_deep_research-163 \
  --timing-out /tmp/163-served-timing.json
# Stop if projected spend > $1; pilot target was ≤ $0.60.

# Full live both sides (expensive; same caps as the pilot):
uv run lgcov bench docs/benchmark --only open_deep_research-163 --live \
  --timing-out /tmp/163-live-timing.json
```

Record wall-clock around the process, plus agent / judges from `--timing-out`, and the
provider usage page for tokens. Append a measured row here only after a real run — do not
treat the estimate table as data.
