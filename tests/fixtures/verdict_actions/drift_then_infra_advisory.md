<!-- lgcov-report -->
## 🔴 lgcov: drift (advisory, not blocking)

2 of 2 runs touched · no KPI judged · root node: `respond`

**Next step:** Open the root node. If the change is intended, re-baseline with `lgcov cassette accept --nodes respond`; if not, fix it. Then fix the harness error and re-run.

Statistical verdict: none (no blocking KPI judged)

Advisory mode: lgcov reports what it found. It does not block this merge and it does not approve this PR. Only a harness error fails this check.

⚠️ Harness error: lgcov could not finish this run. What it found before that stands; anything after it was not checked.

```
L2 gate failed: RuntimeError: boom
```

### Cassette replay

| node | drift | runs |
|---|---|---|
| `respond` | root | 2/2 |

fail-on: `root-drift` · tier: `pr` · root drift: 1 · propagated drift: 0 · verdict: **fail**
