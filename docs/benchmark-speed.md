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
