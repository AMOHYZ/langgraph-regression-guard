<!-- lgcov-report -->
## ⚠️ No decision: needs_human

0 of 2 runs touched · 0 worse · root node: none

**Next step:** Read the pool / lineage diff and decide: this PR changed case pools or lineage.json, which the gate reads from the base, so lgcov can't vouch for it. This check exits 0 because pool and lineage edits need a human. That is not a pass.

Statistical verdict: SHIP

### Cassette replay

No cassette drift (2 runs).

### KPI gate (L2)

| KPI | n | verdict |
|---|---|---|
| tone | 8 | 🟢 SHIP |

fail-on: `root-drift` · tier: `pr` · root drift: 0 · propagated drift: 0 · verdict: **needs_human**
