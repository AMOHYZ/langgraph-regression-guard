## 🔴 lgcov: regression detected

### Graph changes

| change | kind | item |
|---|---|---|
| added | node | `c` |
| removed | edge | `b → __end__` |

### Coverage

| | hit |
|---|---|
| nodes | 50.0% |
| edges | 33.3% |

<details>
<summary>Never hit: 2 nodes, 2 edges</summary>

- node `__end__`
- node `__start__`
- edge `__start__ → a`
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
