# Gate simulations: how often decide() is wrong (T151)

How often does the gate SHIP a PR that dropped a KPI by the full margin, and how often does
it BLOCK a PR that changed nothing? Simulated on 9 Nov 2026 at `53941a3`, on Python 3.12.4,
for one cell: 100 paired binary cases, a base pass rate of 0.95, one sample per case.

**In this cell the gate errs on the safe side.** At a true drop of the margin it SHIPs 0.50% of
runs, under the 2.5% a one-sided 95% bound allows, and 9 of those 10 SHIPs come from the no-drop
path, not the confidence interval. With no change it BLOCKs 0.20% of runs. The cost is
indecision: at 100 cases it leaves 38% of unchanged PRs INCONCLUSIVE.

## The cell

| | |
|---|---|
| Cases N | 100, paired, binary, K = 1 |
| Base pass rate | 0.95 |
| ρ (base vs PR, from the 2x2 table) | 0.688, asked for 0.7 |
| Margin m | 0.05, `GateConfig`'s default |
| α | 0.05, `GateConfig`'s default |
| Runs R | 2,000 per arm, base seed 0 |

ρ = 0.7 is impossible when the PR's pass rate is 0.90: it needs p11 = 0.9008, and p11, the share
of cases passing on both sides, can't be more than 0.90. The largest ρ those rates allow is
0.688, so both arms use 0.688: the two rates come from one cell. At 0.688 the PR never fixes a
case the base failed; every discordant pair at Δ = −m is a regression.

## Method

Each run draws 100 pairs from the base-vs-PR 2x2 table that has these pass rates and this ρ,
then judges them the way the gate does: `paired_test` (exact McNemar, BCa 95% CI), then
`decide()` with one KPI, so Holm's test is p ≤ α. Two arms:

- **Δ = 0**, the PR's rate equal to the base's: BLOCK is the wrong call.
- **Δ = −m**, the PR's rate 0.90: SHIP is the wrong call. The true drop sits on the margin.

Every run's seed is spawned from the base seed, with the data and the bootstrap on separate
streams, so a rerun gives the same table. The Monte Carlo error is the binomial SE
√(r(1 − r)/R) and the Wilson 95% interval.

## Results

N=100, p_base=0.95, ρ=0.688 (clamped from 0.7), m=0.05, α=0.05, R=2,000 per arm, seed 0.

| Δ | Wrong call | Rate | MC SE | Wilson 95% | No-drop SHIPs (share of SHIPs) | No-drop SHIP rate | SHIP / BLOCK / INCONCLUSIVE |
|---|---|---|---|---|---|---|---|
| 0 | BLOCK | 0.20% | 0.10% | 0.08%–0.51% | 95 (7.67%) | 4.75% | 1238 / 4 / 758 |
| −0.05 | SHIP | 0.50% | 0.16% | 0.27%–0.92% | 9 (90.00%) | 0.45% | 10 / 762 / 1228 |

## What it shows

**False SHIP comes from the no-drop path.** With no discordant pair among 100 cases, `decide()`
SHIPs because its no-drop bound, 3.69 / 100 = 0.037, is under the margin. At Δ = −m that happens
whenever none of the 100 cases regresses: 0.95¹⁰⁰ = 0.59% of runs, and 0.45% here, within the
Monte Carlo error. The confidence interval wrongly SHIPs 1 run in 2,000. At this N the no-drop
path, not the CI, sets the false-SHIP rate.

**False BLOCK is rare.** BLOCK needs the whole CI below 0, the drop at least the margin and a
significant test; with no change, 4 runs in 2,000 met all three.

**100 cases leave the gate undecided.** An unchanged PR SHIPs 62% of the time; the rest are
INCONCLUSIVE. A drop of the full margin is BLOCKed 38% of the time. Neither is a wrong call, but
both are a human's to settle.

## Limits

One cell. The M1 grid, N in {50, 100, 200, 800} × ρ in {0.3, 0.7} × p in {0.80, 0.95}, is
not run yet: `sweep()` takes it as a list of cells and skips the ones it can't run, with the
reason. Binary KPIs with one sample per case only: graded scores and K > 1 go through the
sign-flip test, which this does not cover. One KPI, so no multiplicity correction is tested.

## Rerun

```bash
uv run python -m lgcov.gate_sim 2000 0
```

The arguments are runs per arm and the base seed. It takes about 70 seconds.
