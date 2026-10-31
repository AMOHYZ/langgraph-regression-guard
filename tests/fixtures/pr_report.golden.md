## 🔴 lgcov: regression detected

### Graph changes

| change | kind | item |
|---|---|---|
| added | node | `c` |
| removed | edge | `b → __end__` |

### Coverage

| | hit |
|---|---|
| nodes | 75% (no baseline yet) |
| edges | 33.3% (no baseline yet) |

<details>
<summary>Never hit: 1 nodes, 2 edges</summary>

- node `b`
- edge `a → b`
- edge `b → __end__`

</details>

### Judge verdicts

| KPI | verdict | confidence | note |
|---|---|---|---|
| completeness | 🟡 needs_human | 0.55 | low judge confidence |
| faithfulness | 🟢 pass | 0.92 | — |
| tone | 🔴 fail | 0.81 | score drop 0.18 > 0.05 |

---
Reviewer: reply `accept`, `reject` or `override` per node to update the baseline (L3).
