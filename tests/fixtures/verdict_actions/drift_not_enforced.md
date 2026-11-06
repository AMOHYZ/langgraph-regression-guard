<!-- lgcov-report -->
## ⚠️ No decision: needs_human

**Next step:** Read the drift table and decide. `fail-on: never` means lgcov won't fail this check on drift. This check exits 0 because `fail-on: never` does not block on drift. That is not a pass.

Statistical verdict: none (no blocking KPI judged)

### Cassette replay

| node | drift | runs |
|---|---|---|
| `respond` | root | 2/2 |

fail-on: `never` · tier: `pr` · root drift: 1 · propagated drift: 0 · verdict: **needs_human**
