<!-- lgcov-report -->
## ⚠️ No decision: needs_human

0 of 2 runs touched · 0 worse · root node: none

**Next step:** lgcov can't attribute this change safely: occurrence pairing is unsafe: node 'worker' ran 2x, baseline has 3x. Decide yourself; this is not a pass or a fail of the agent. This check exits 0 because attribution was unsupported. That is not a pass.

Statistical verdict: SHIP

### Cassette replay

No cassette drift (2 runs).

### KPI gate (L2)

| KPI | n | verdict |
|---|---|---|
| tone | 8 | 🟢 SHIP |

fail-on: `root-drift` · tier: `pr` · root drift: 0 · propagated drift: 0 · verdict: **needs_human**
