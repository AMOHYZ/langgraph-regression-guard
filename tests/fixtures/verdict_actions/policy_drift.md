<!-- lgcov-report -->
## 🔴 lgcov: drift

**Next step:** Open the root node. If the change is intended, re-baseline with `lgcov cassette accept --nodes respond`; if not, fix it.

Statistical verdict: SHIP

### Cassette replay

| node | drift | runs |
|---|---|---|
| `respond` | root | 2/2 |

### KPI gate (L2)

| KPI | n | verdict |
|---|---|---|
| tone | 8 | 🟢 SHIP |

fail-on: `root-drift` · tier: `pr` · root drift: 1 · propagated drift: 0 · verdict: **fail**
