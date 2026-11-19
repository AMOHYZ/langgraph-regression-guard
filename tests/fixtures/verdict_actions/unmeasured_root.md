<!-- lgcov-report -->
## ⚠️ No decision: needs_human

2 of 2 runs touched · 0 worse · root node: `respond`

**Next step:** Read the unmeasured root(s) and decide, or add a KPI/code check that covers them. `fail-on: harmed-root` never treats unmeasured drift as a pass. This check exits 0 because unmeasured roots under `fail-on: harmed-root` need a human. That is not a pass.

Statistical verdict: SHIP

### Cassette replay

| node | drift | runs |
|---|---|---|
| `respond` | root | 2/2 |

### KPI gate (L2)

| KPI | n | verdict |
|---|---|---|
| tone | 8 | 🟢 SHIP |

fail-on: `harmed-root` · tier: `pr` · root drift: 1 · propagated drift: 0 · harm: `respond` unmeasured · verdict: **needs_human**
