<!-- lgcov-report -->
## ⚠️ Advisory: policy comes from this PR and is not trusted yet; enforcement starts after merge

2 of 2 runs touched · no KPI judged · root node: `respond`

**Next step:** Review this PR's lgcov setup like any other config change. The results below are what lgcov would have said; they block nothing. Then fix the harness error and re-run.

Statistical verdict: none (no blocking KPI judged)

⚠️ Harness error: lgcov could not finish this run. What it found before that stands; anything after it was not checked.

```
L2 gate failed: RuntimeError: boom
```

### Cassette replay

| node | drift | runs |
|---|---|---|
| `respond` | root | 2/2 |

fail-on: `root-drift` · tier: `pr` · root drift: 1 · propagated drift: 0 · verdict: **advisory**
