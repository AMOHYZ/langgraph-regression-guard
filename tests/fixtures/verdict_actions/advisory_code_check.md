<!-- lgcov-report -->
## 🔴 lgcov: code check failed (advisory, not blocking)

**Next step:** `tone` passed on the base and fails here on 1 of 8 cases (`run-0/respond`). Fix the regression. If a check can legitimately vary between runs, make it advisory in `gate.advisory_kpis` on the base branch first: this PR can't change what grades it.

Statistical verdict: SHIP

Advisory mode: lgcov reports what it found. It does not block this merge and it does not approve this PR. Only a harness error fails this check.

### KPI gate (L2)

| KPI | n | verdict |
|---|---|---|
| tone | 8 | 🟢 SHIP |

fail-on: `root-drift` · tier: `pr` · root drift: 0 · propagated drift: 0 · verdict: **fail**
