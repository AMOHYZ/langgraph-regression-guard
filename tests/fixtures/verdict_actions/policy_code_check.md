<!-- lgcov-report -->
## 🔴 lgcov: code check failed

2 of 2 runs touched · 1 worse (`tone` 1) · root node: `respond`

**Next step:** `tone` passed on the base and fails here on 1 of 8 cases (`run-0/respond`). Fix the regression. If a check can legitimately vary between runs, make it advisory in `gate.advisory_kpis` on the base branch first: this PR can't change what grades it.

Statistical verdict: SHIP

### Cassette replay

| node | drift | runs |
|---|---|---|
| `respond` | root | 2/2 |

### KPI gate (L2)

| KPI | n | verdict |
|---|---|---|
| tone | 8 | 🟢 SHIP |
| 🔴 new failure: tone on 1 of 8 cases: `run-0/respond` |

fail-on: `root-drift` · tier: `pr` · root drift: 1 · propagated drift: 0 · verdict: **fail**
