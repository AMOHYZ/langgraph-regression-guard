<!-- lgcov-report -->
## 🔴 lgcov: KPI regression (advisory, not blocking)

**Next step:** Fix the root node in the KPI table. If the judge is wrong, record it with `lgcov review override`.

Statistical verdict: BLOCK

Advisory mode: lgcov reports what it found. It does not block this merge and it does not approve this PR. Only a harness error fails this check.

### KPI gate (L2)

| KPI | n | verdict |
|---|---|---|
| tone | 8 | 🔴 BLOCK |

fail-on: `root-drift` · tier: `pr` · root drift: 0 · propagated drift: 0 · verdict: **fail**
