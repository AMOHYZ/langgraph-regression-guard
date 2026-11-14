<!-- lgcov-report -->
## 🟢 lgcov: every node served, nothing re-ran

0 of 2 runs touched · 0 worse · root node: none

**Next step:** Review as usual. lgcov measured nothing here: every node's code and masked input match the recording, so it was served. A change that reaches only a masked value, such as a run id or a message's usage, or a Store isn't seen.

Statistical verdict: SHIP

### Cassette replay

No cassette drift (2 runs).

### KPI gate (L2)

| KPI | n | verdict |
|---|---|---|
| tone | 8 | 🟢 SHIP |

⏱ 12 s · agent 9 s (0 live / 5 served) · judges 3 s (8 calls) · budget 90 s

fail-on: `root-drift` · tier: `pr` · root drift: 0 · propagated drift: 0 · verdict: **pass**
