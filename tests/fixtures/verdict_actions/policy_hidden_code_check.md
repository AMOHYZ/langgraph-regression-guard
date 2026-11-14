<!-- lgcov-report -->
## 🔴 code check failed (hidden by a re-baseline in this PR)

0 of 2 runs touched · 1 worse (`tone` 1) · root node: none

**Next step:** `tone` passed on the base's cassettes and fails here on 1 of 8 cases (`run-0/respond`). Fix the regression. If the new behaviour is right, change the check on the base branch first: this PR can't change what grades it.

Statistical verdict: SHIP

### Cassette replay

No cassette drift (2 runs).

### KPI gate (L2)

| KPI | n | verdict |
|---|---|---|
| tone | 8 | 🟢 SHIP |

⚠️ This PR changes `cassettes`, the expected outputs this gate compares.

fail-on: `root-drift` · tier: `pr` · root drift: 0 · propagated drift: 0 · verdict: **fail**
