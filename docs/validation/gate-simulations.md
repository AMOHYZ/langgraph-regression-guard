# Gate simulations: how often decide() is wrong (T151)

How often does the gate SHIP a PR that dropped a KPI by the full margin, and how often does
it BLOCK a PR that changed nothing? Part 1 (9 Nov 2026) simulated one cell. Part 2 (17 Nov)
runs the M1 grid with power arms, missing pairs, graded scores and several KPIs, and puts two
other lower bounds next to the gate's.

## Headline

Part 2's main grid: binary scores, one KPI, every pair, 5,000 runs per arm, m and α at
`GateConfig`'s 0.05.

**The smallest N at which the measured false SHIP is within 2.5% is N = 200.** Within means the
false-SHIP rate's Wilson 95% interval reaches 2.5% in every cell at that N and at every larger
N, the clamped row included. By scenario it is 200 with 10% of pairs lost at random and on the
graded path, 50 with 2 or 4 KPIs, and no N when half of the PR's failing results are lost.

- **At N = 50 the gate SHIPs too many real drops.** A PR that dropped a KPI by the full margin
  SHIPs 3.66%, 6.06% and 5.48% of the time in the three cells that run unclamped, each above
  2.5% beyond Monte Carlo error. The clamped cell SHIPs none at N = 50: every discordant pair
  there is a regression, and the no-drop bound, 3.69 / 50 = 0.074, is over the margin. At
  N = 100 one cell is still above: 3.22% (Wilson 2.77–3.75%) at p = 0.80, ρ = 0.7.
- **Measured power falls short of predicted.** In 19 of the 36 arms with a prediction, the gate
  makes the right call less often than its sizing formulas predict, beyond Monte Carlo error.
  The gap is widest where few pairs are discordant. In part 1's cell an unchanged PR SHIPs 61.6%
  of the time at N = 100 against 82.8% predicted, and 31.2% against 53.8% at N = 50. At N = 50 a
  drop of 0.10 is BLOCKed 34.7% of the time against 55.6% predicted (p = 0.80, ρ = 0.7). By
  N = 800 every gap is under a point.
- **Part 1's 38% holds.** In its cell 38.3% of unchanged PRs are INCONCLUSIVE at N = 100, and
  the first 2,000 of these runs are part 1's own. It is 68.8% at N = 50, 7.5% at N = 200 and none
  at N = 800. The other cells leave 54–82% INCONCLUSIVE at N = 100, and p = 0.80 with ρ = 0.3
  still leaves 15.6% at N = 800.
- **When the PR's failing results go missing, real drops SHIP, and more so as N grows.** With
  half of them lost, a drop of the margin SHIPs 22–30% of the time at N = 50 in the unclamped
  cells, and 92–99.6% in every cell at N = 800. Only failures go missing, so the PR looks better
  than it is, and more cases make the gate surer of that.
- **With Tango's bound in place of BCa, false SHIP stays within 2.5%** in every cell at every N,
  with the same test and no-drop path. The cost is at N = 50, where unchanged PRs SHIP 8–12% of
  the time against the gate's 14–33%.

This work changes nothing in the gate.

## Part 1: one cell (9 Nov 2026)

Simulated at `53941a3`, on Python 3.12.4, for one cell: 100 paired binary cases, a base pass
rate of 0.95, one sample per case.

**In this cell the gate errs on the safe side.** At a true drop of the margin it SHIPs 0.50% of
runs, under the 2.5% a one-sided 95% bound allows, and 9 of those 10 SHIPs come from the no-drop
path, not the confidence interval. With no change it BLOCKs 0.20% of runs. The cost is
indecision: at 100 cases it leaves 38% of unchanged PRs INCONCLUSIVE.

### The cell

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

### Method

Each run draws 100 pairs from the base-vs-PR 2x2 table that has these pass rates and this ρ,
then judges them the way the gate does: `paired_test` (exact McNemar, BCa 95% CI), then
`decide()` with one KPI, so Holm's test is p ≤ α. Two arms:

- **Δ = 0**, the PR's rate equal to the base's: BLOCK is the wrong call.
- **Δ = −m**, the PR's rate 0.90: SHIP is the wrong call. The true drop sits on the margin.

Every run's seed is spawned from the base seed, with the data and the bootstrap on separate
streams, so a rerun gives the same table. The Monte Carlo error is the binomial SE
√(r(1 − r)/R) and the Wilson 95% interval.

### Results

N=100, p_base=0.95, ρ=0.688 (clamped from 0.7), m=0.05, α=0.05, R=2,000 per arm, seed 0.

| Δ | Wrong call | Rate | MC SE | Wilson 95% | No-drop SHIPs (share of SHIPs) | No-drop SHIP rate | SHIP / BLOCK / INCONCLUSIVE |
|---|---|---|---|---|---|---|---|
| 0 | BLOCK | 0.20% | 0.10% | 0.08%–0.51% | 95 (7.67%) | 4.75% | 1238 / 4 / 758 |
| −0.05 | SHIP | 0.50% | 0.16% | 0.27%–0.92% | 9 (90.00%) | 0.45% | 10 / 762 / 1228 |

### What it shows

**False SHIP comes from the no-drop path.** With no discordant pair among 100 cases, `decide()`
SHIPs because its no-drop bound, 3.69 / 100 = 0.037, is under the margin. At Δ = −m that happens
whenever none of the 100 cases regresses: 0.95¹⁰⁰ = 0.59% of runs, and 0.45% here, within the
Monte Carlo error. The confidence interval wrongly SHIPs 1 run in 2,000. At this N the no-drop
path, not the CI, sets the false-SHIP rate.

**False BLOCK is rare.** BLOCK needs the whole CI below 0, the drop at least the margin and a
significant test; with no change, 4 runs in 2,000 met all three.

**100 cases leave the gate undecided.** An unchanged PR SHIPs 62% of the time; the rest are
INCONCLUSIVE. A drop of the full margin is BLOCKed 38% of the time. Neither is a wrong call, but
both are a human's to settle. At N = 100, the 38% of unchanged PRs left INCONCLUSIVE is the gate
declining to call it on thin data, not a defect: more cases (N = 200 and up) are the lever.
Part 2 measures that across the grid, and its clamped row is this cell at each N.

### Limits

One cell, one KPI, binary scores with one sample per case. Part 2 runs the grid, missing
pairs, several KPIs and graded scores.

## Part 2: the M1 grid (17 Nov 2026)

Simulated at `636a28f`, on Python 3.12.4, on 17 Nov 2026: 2 h 56 min on 6 workers, base seed 0.
Every skip below was logged with its reason.

### What runs

| | |
|---|---|
| Cells | N ∈ {50, 100, 200, 800} × ρ ∈ {0.3, 0.7} × p ∈ {0.80, 0.95} |
| Arms | Δ = 0 and Δ = −m, where BLOCK and SHIP are wrong; Δ = −0.10, where BLOCK is right; Δ = +0.05, where SHIP is right |
| Margin m, α | 0.05 and 0.05, `GateConfig`'s defaults |
| Runs R | 5,000 per arm for the main grid and the lower bounds; 1,000 per arm for the other scenarios |
| Seed | 0, each run's seed spawned from it with stdlib sha256 |

**Skipped, never clamped.** One ρ serves every arm of a cell. A cell whose error arms its ρ
can't reach is skipped with its reason, and a power arm its ρ can't reach is skipped on its own.
The four p = 0.95, ρ = 0.7 cells skip: ρ = 0.7 needs p11 ≤ 0.90 at Δ = −m, and the reachable ρ
is 0.688. Δ = +0.05 skips at p = 0.95, where the PR's rate would be 1.00 and ρ is undefined.

**One extra row, labelled clamped:** part 1's own cell, p = 0.95 with ρ clamped from 0.7 to
0.688, at each N. It runs the error arms only: Δ = −0.10 allows ρ up to 0.546, and Δ = +0.05
makes the PR's rate 1.00. At N = 100 its first 2,000 runs per arm are part 1's runs.

### How each run is judged

Each run draws N pairs and judges them with the gate's own code: `paired_test` (exact McNemar,
BCa 95% CI), then `gate_kpis` (Holm across the K_gate blocking KPIs, then `decide()` for each)
and `gate_findings` (the PR's verdict: SHIP only if every KPI SHIPs, BLOCK if any BLOCKs). The
judge is taken as sure, so no BLOCK goes to a human. With one KPI this is part 1's `decide()`
call, and `python -m lgcov.gate_sim 2000 0` still prints part 1's table, byte for byte.

The scenarios, each on the same cells:

- **Binary, one KPI, every pair:** the main grid.
- **10% of pairs lost at random:** each pair is lost with probability 0.10, and the run drops
  it, as the gate drops a case with no valid score on a side.
- **Half of the PR's failing results lost:** a PR's bad outputs can make the judge error, so
  here only failures go missing. Half of the PR's failing results are lost and none of its
  passes: 2.5% to 15% of pairs, by the PR's rate. Both lossy scenarios lose pairs on the same
  draws.
- **2 and 4 KPIs, Holm:** independent binary KPIs on the same cases. KPI 1 takes the arm's Δ and
  the others stay at Δ = 0. KPI 1 sees the same pairs as in the main grid.
- **Graded, K = 3 samples per case:** each sample is scored 0, 0.5 or 1, so `paired_test` takes
  its sign-flip path (10,000 flips) and puts the BCa CI on case means, as the gate does at
  nightly's k = 3. A case's pass probability on each side is Φ(a + z), with the base and PR z's
  correlated ρ and spread σ = 1. Each sample is Binomial(2, that probability) / 2, and a is set
  so the mean score is exactly p on the base and p + Δ on the PR. There is no 2×2 table, so
  p = 0.95 with ρ = 0.7 runs here. ρ is the correlation of the cases' latent effects; the
  results give the observed correlation of case means next to it.

**The lower bounds.** With one KPI and every pair, each run also builds two other 95%
intervals from its 2×2 counts. One is Tango's asymptotic score interval (Tango 1998). The other
is SCAS-style, derived here: Tango's score with Laud's skewness correction and N/(N − 1)
variance factor (Laud 2017). Laud gives those for two independent samples; the paired third
moment is this repo's own derivation. The gate's own bound is BCa's, or the no-drop bound when
no pair is discordant, since that is what SHIP reads then. For each arm the run counts how
often each lower bound sat above the true Δ (nominal 2.5%), and what `decide()` would have
called with each CI in place of BCa, keeping the same McNemar test and no-drop path.

**Predicted power** is the chance of the right call by the normal approximation the gate sizes
N with: `ni_required_n`'s for SHIP, and `required_n`'s for BLOCK at α / K_gate, as the gate
sizes N*. The graded model has no such formula.

**Monte Carlo error.** Every rate has its binomial SE √(r(1 − r)/R) and its Wilson 95%
interval. Cells and scenarios share random streams (common random numbers), so each rate's MC
error is as stated, and differences between two rates are measured more precisely than their
two SEs suggest. Results don't depend on the number of workers; a test checks it.

### Results

Bold marks a rate whose Wilson 95% interval lies wholly above its nominal level and, in the
power table, a measured rate short of the prediction beyond Monte Carlo error. The MC SE of a
2.5% rate is 0.22 points at R = 5,000 and 0.49 at R = 1,000.

**Wrong SHIP at a drop of the margin, Δ = −m.** Main grid, with Wilson 95% intervals:

| p, ρ | N = 50 | N = 100 | N = 200 | N = 800 |
|---|---|---|---|---|
| 0.80, 0.3 | **3.66%** (3.17–4.22%) | 2.32% (1.94–2.78%) | 2.52% (2.12–2.99%) | 2.42% (2.03–2.88%) |
| 0.80, 0.7 | **6.06%** (5.43–6.76%) | **3.22%** (2.77–3.75%) | 2.52% (2.12–2.99%) | 2.84% (2.41–3.34%) |
| 0.95, 0.3 | **5.48%** (4.88–6.15%) | 2.38% (1.99–2.84%) | 2.30% (1.92–2.75%) | 2.16% (1.79–2.60%) |
| 0.95, 0.688 (clamped) | 0.00% (0.00–0.08%) | 0.50% (0.34–0.74%) | 1.32% (1.04–1.68%) | 2.14% (1.77–2.58%) |

The no-drop path makes 21 of the clamped cell's 25 wrong SHIPs at N = 100, 1 of its 66 at
N = 200, and none anywhere else.

**No change, Δ = 0: false BLOCK / INCONCLUSIVE.** Main grid:

| p, ρ | N = 50 | N = 100 | N = 200 | N = 800 |
|---|---|---|---|---|
| 0.80, 0.3 | 1.42% / 84.7% | 1.58% / 81.8% | 2.02% / 68.4% | 0.12% / 15.6% |
| 0.80, 0.7 | 0.58% / 71.3% | 1.30% / 65.4% | 1.18% / 41.0% | 0.00% / 0.5% |
| 0.95, 0.3 | 0.12% / 67.1% | 0.62% / 54.4% | 0.44% / 27.1% | 0.00% / 0.1% |
| 0.95, 0.688 (clamped) | 0.00% / 68.8% | 0.10% / 38.3% | 0.02% / 7.5% | 0.00% / 0.0% |

False BLOCK never reaches 2.5%. A BLOCK needs the whole 95% CI below 0, so 2.5% is its nominal
level; the most measured is 2.02% (Wilson 1.67–2.45%). A drop of 0.10 SHIPs at most 0.70% of the
time, and a gain of 0.05 BLOCKs at most 0.16%.

**The right call, measured / predicted, in %.** Main grid. Predicted is the normal
approximation the gate sizes N with:

| p, ρ | Δ, right call | N = 50 | N = 100 | N = 200 | N = 800 |
|---|---|---|---|---|---|
| 0.80, 0.3 | 0, SHIP | 13.9 / 11.3 | **16.6 / 18.3** | **29.6 / 32.1** | 84.3 / 84.8 |
| 0.80, 0.3 | −0.10, BLOCK | **19.0 / 28.3** | **40.8 / 50.0** | **75.3 / 79.2** | **99.8 / 100.0** |
| 0.80, 0.3 | +0.05, SHIP | 40.2 / 35.0 | **59.0 / 60.5** | 87.7 / 88.3 | 100.0 / 100.0 |
| 0.80, 0.7 | 0, SHIP | 28.1 / 20.6 | **33.3 / 36.5** | **57.8 / 62.6** | 99.5 / 99.5 |
| 0.80, 0.7 | −0.10, BLOCK | **34.7 / 55.6** | **81.3 / 84.4** | 98.8 / 98.8 | 100.0 / 100.0 |
| 0.80, 0.7 | +0.05, SHIP | 75.9 / 66.6 | 92.6 / 92.2 | 99.8 / 99.8 | 100.0 / 100.0 |
| 0.95, 0.3 | 0, SHIP | 32.8 / 27.8 | **45.0 / 49.2** | **72.5 / 78.3** | **99.9 / 100.0** |
| 0.95, 0.3 | −0.10, BLOCK | **32.1 / 50.6** | **73.8 / 79.7** | 97.7 / 97.7 | 100.0 / 100.0 |
| 0.95, 0.688 (clamped) | 0, SHIP | **31.2 / 53.8** | **61.6 / 82.8** | **92.5 / 98.4** | 100.0 / 100.0 |

Measured beats predicted beyond Monte Carlo error in five arms, all at N = 50, where BCa's
lower bound often sits too high (next table).

**The lower bounds: gate (BCa) / Tango / SCAS-style.** Main grid. Wrong SHIP at Δ = −m, in %:

| p, ρ | N = 50 | N = 100 | N = 200 | N = 800 |
|---|---|---|---|---|
| 0.80, 0.3 | **3.66** / 2.38 / 2.34 | 2.32 / 2.24 / 2.26 | 2.52 / 2.58 / 2.58 | 2.42 / 2.54 / 2.54 |
| 0.80, 0.7 | **6.06** / 1.70 / 2.28 | **3.22** / 2.02 / **2.94** | 2.52 / 2.26 / 2.30 | 2.84 / 2.80 / 2.90 |
| 0.95, 0.3 | **5.48** / 1.30 / 2.20 | 2.38 / 1.74 / 2.34 | 2.30 / 2.04 / 2.06 | 2.16 / 2.10 / 2.20 |
| 0.95, 0.688 (clamped) | 0.00 / 0.00 / 0.00 | 0.50 / 0.42 / **3.50** | 1.32 / 0.88 / 2.74 | 2.14 / 1.66 / 2.58 |

SHIP at Δ = 0, the right call, in %:

| p, ρ | N = 50 | N = 100 | N = 200 | N = 800 |
|---|---|---|---|---|
| 0.80, 0.3 | 13.9 / 8.6 / 8.6 | 16.6 / 16.2 / 16.5 | 29.6 / 30.4 / 30.4 | 84.3 / 84.8 / 84.8 |
| 0.80, 0.7 | 28.1 / 11.6 / 13.4 | 33.3 / 28.3 / 32.1 | 57.8 / 56.8 / 56.9 | 99.5 / 99.4 / 99.4 |
| 0.95, 0.3 | 32.8 / 12.0 / 17.9 | 45.0 / 35.9 / 44.8 | 72.5 / 70.1 / 71.3 | 99.9 / 99.9 / 99.9 |
| 0.95, 0.688 (clamped) | 31.2 / 8.3 / 25.0 | 61.6 / 53.8 / 68.2 | 92.5 / 91.0 / 93.0 | 100.0 / 100.0 / 100.0 |

Over all 52 arms, Tango's bound never sits above the true Δ more often than 2.5% beyond Monte
Carlo error. BCa's does in 12: 7 at N = 50, 4 at N = 100, and the clamped cell's Δ = 0 at
N = 200. At Δ = −m that is the false SHIP above; at Δ = 0 and +0.05 it SHIPs PRs that should
SHIP. The SCAS-style bound does in two arms at N = 100, both at Δ = −m: 2.94% at p = 0.80,
ρ = 0.7, and 3.50% in the clamped cell, where every discordant pair is a regression. From
N = 200 up, the three bounds SHIP unchanged PRs within 2.4 points of each other.

**The other scenarios: wrong SHIP at Δ = −m, in %.** 1,000 runs per arm:

| Scenario | p, ρ | N = 50 | N = 100 | N = 200 | N = 800 |
|---|---|---|---|---|---|
| 10% of pairs lost at random | 0.80, 0.3 | **4.8** | **3.6** | 3.1 | 2.6 |
| | 0.80, 0.7 | **7.9** | **4.4** | 3.2 | 2.5 |
| | 0.95, 0.3 | **6.4** | **4.3** | 2.8 | 2.4 |
| | 0.95, 0.688 (clamped) | 0.0 | 0.9 | 2.4 | 2.5 |
| Half of the PR's failing results lost | 0.80, 0.3 | **30.0** | **39.6** | **64.7** | **99.6** |
| | 0.80, 0.7 | **23.6** | **27.7** | **44.4** | **94.2** |
| | 0.95, 0.3 | **22.1** | **29.8** | **51.1** | **97.3** |
| | 0.95, 0.688 (clamped) | 0.0 | **8.2** | **30.4** | **92.2** |
| 2 KPIs, Holm | 0.80, 0.3 | 0.7 | 0.4 | 1.1 | 2.0 |
| | 0.80, 0.7 | 2.0 | 0.9 | 1.5 | 3.2 |
| | 0.95, 0.3 | 1.6 | 0.9 | 1.9 | 2.6 |
| | 0.95, 0.688 (clamped) | 0.0 | 0.3 | 1.4 | 2.6 |
| 4 KPIs, Holm | 0.80, 0.3 | 0.1 | 0.0 | 0.2 | 1.6 |
| | 0.80, 0.7 | 0.1 | 0.1 | 0.5 | 3.1 |
| | 0.95, 0.3 | 0.3 | 0.3 | 0.9 | 2.6 |
| | 0.95, 0.688 (clamped) | 0.0 | 0.2 | 1.2 | 2.6 |
| Graded, K = 3 | 0.80, 0.3 | 2.4 | 2.8 | 2.4 | 3.1 |
| | 0.80, 0.7 | 2.4 | 2.1 | 2.2 | 2.8 |
| | 0.95, 0.3 | **3.8** | **3.5** | 2.3 | 2.7 |
| | 0.95, 0.7 | **4.4** | 2.8 | 2.4 | 1.9 |

- **10% of pairs lost at random:** 9.7–10.1% of pairs were lost. False SHIP is above 2.5% at
  N = 50 and 100 in the three unclamped cells, and within from N = 200. False BLOCK at Δ = 0 is
  at most 2.2%.
- **Half of the PR's failing results lost:** 2.5–15.2% of pairs were lost, more at p = 0.80.
  Only the PR's failures go missing, so its measured pass rate is biased up: false SHIP rises
  with N, and a drop of 0.10, where SHIP is wrong too, SHIPs up to 65.0% of the time (p = 0.80,
  ρ = 0.3, N = 800). Unchanged PRs SHIP far more often than predicted, and drops of 0.10 are
  BLOCKed far less (1.1–55.1% against 28–100% predicted), so predicted power means nothing
  here. False BLOCK is at most 0.2%.
- **2 and 4 KPIs, Holm:** SHIP needs every KPI to SHIP, so false SHIP stays within 2.5% at every
  N. The most, 3.2% at N = 800, is within Monte Carlo error: there the unchanged KPIs nearly
  always SHIP, and KPI 1 decides. Holm keeps the family's false BLOCK at Δ = 0 to 1.9% or less
  with 2 KPIs and 1.8% with 4. The price is indecision: at N = 100, 64.6–96.1% of unchanged PRs
  are INCONCLUSIVE with 2 KPIs, and 87.3–99.1% with 4.
- **Graded, K = 3:** false SHIP is above 2.5% at p = 0.95 through N = 100, and within from
  N = 200. The case means correlate 0.11–0.20 at latent ρ = 0.3, and 0.34–0.48 at 0.7. False
  BLOCK at Δ = 0 is at most 3.1% (Wilson 2.19–4.37%, not beyond Monte Carlo error). At N = 100,
  9.1–61.8% of unchanged PRs are INCONCLUSIVE.

### Every deviation from nominal

The nominal levels: 2.5% for a wrong SHIP and for a lower bound above the true Δ (one side of a
95% interval), 2.5% for a false BLOCK on one KPI, and α = 5% for the family under Holm. A rate
deviates when its whole Wilson 95% interval lies above its level.

**Wrong SHIP above 2.5%**, at Δ = −m unless marked:

- Main grid: N = 50 in the three unclamped cells (3.66%, 6.06%, 5.48%), and N = 100 at
  p = 0.80, ρ = 0.7 (3.22%).
- 10% of pairs lost at random: N = 50 (4.8%, 7.9%, 6.4%) and N = 100 (3.6%, 4.4%, 4.3%), in
  the three unclamped cells.
- Half of the PR's failing results lost: every cell at every N but the clamped cell at N = 50,
  from 8.2% to 99.6%. At Δ = −0.10, 10 of the 12 arms, from 4.0% to 65.0%: all but p = 0.80,
  ρ = 0.7 at N = 200 and 800.
- Graded, K = 3: p = 0.95 at N = 50 (3.8% at ρ = 0.3, 4.4% at ρ = 0.7), and at N = 100 (3.5% at
  ρ = 0.3).
- 2 and 4 KPIs: none.

**Lower bound above the true Δ more often than 2.5%**, main grid, all four arms:

- The gate's (BCa), 12 of 52 arms. At N = 50: p = 0.80, ρ = 0.3 at Δ = −m (3.66%) and +0.05
  (3.56%); p = 0.80, ρ = 0.7 at 0 (4.12%), −m (6.06%) and +0.05 (3.16%); p = 0.95, ρ = 0.3 at 0
  (4.52%) and −m (5.48%). At N = 100: p = 0.80, ρ = 0.7 at 0 and −m (3.22% each); p = 0.95,
  ρ = 0.3 at 0 (3.84%); the clamped cell at 0 (4.04%). At N = 200: the clamped cell at 0
  (4.10%).
- Tango's: none.
- The SCAS-style one: at N = 100 and Δ = −m, p = 0.80, ρ = 0.7 (2.94%) and the clamped cell
  (3.50%).

**False BLOCK:** none. The highest are 2.02% in the main grid (Wilson 1.67–2.45%), 2.2% with
pairs lost at random, 0.2% with the PR's failures lost, 3.1% on the graded path (2.19–4.37%),
and for the family, at Δ = 0 or +0.05, 2.0% with 2 KPIs and 1.8% with 4.

**The right call short of predicted beyond Monte Carlo error:**

- Main grid: the 19 bold entries in the power table.
- 10% of pairs lost at random, 16 arms. BLOCK at Δ = −0.10 in 10 of 12: all but N = 800 at
  p = 0.80, ρ = 0.7 and at p = 0.95, ρ = 0.3. SHIP at Δ = 0 at p = 0.80, ρ = 0.3, N = 800; at
  N = 200 for p = 0.80, ρ = 0.7 and p = 0.95, ρ = 0.3; and in the clamped cell at N = 50, 100
  and 200. The prediction counts every pair, so part of this is the 10% lost.
- Half of the PR's failing results lost, 14 arms: BLOCK at Δ = −0.10 in all 12, and SHIP at
  Δ = 0 in the clamped cell at N = 50 and 100. Measured is far above predicted in 16 more arms,
  SHIP at Δ = 0 or +0.05: that is the bias, not power.
- 2 KPIs, 13 arms. BLOCK at Δ = −0.10 at N = 50 and 100 in the three unclamped cells, and at
  N = 200 for p = 0.80, ρ = 0.3. SHIP at Δ = 0 at p = 0.80, ρ = 0.7, N = 200; at N = 100 and 200
  for p = 0.95, ρ = 0.3; and in the clamped cell at N = 50, 100 and 200.
- 4 KPIs, 15 arms. BLOCK at Δ = −0.10 in the same 7 arms as with 2 KPIs. SHIP at Δ = 0 at
  p = 0.80, ρ = 0.3, N = 800; at N = 100 and 200 for p = 0.80, ρ = 0.7 and p = 0.95, ρ = 0.3;
  and in the clamped cell at N = 50, 100 and 200.
- Graded: no prediction to compare with.

Outside the lost-failures scenario, measured beats predicted beyond Monte Carlo error only at
N = 50: in five arms of the main grid, five each with pairs lost at random and with 2 KPIs, and
four with 4 KPIs.

**Design deviations:**

- Skipped, never clamped, each with its logged reason: the four binary p = 0.95, ρ = 0.7 cells;
  Δ = +0.05 at p = 0.95, on both paths; the clamped row's Δ = −0.10 and +0.05 arms.
- The clamped row runs at ρ = 0.688, asked for 0.7.
- On the graded path ρ is the latent correlation, and the case means correlate less: 0.11–0.20
  at 0.3, and 0.34–0.48 at 0.7.
- Pairs actually lost: 9.74–10.11% where 10% was asked, and 2.48–15.15% where half of the PR's
  failures were.
- Predicted power counts all N pairs, so it doesn't allow for lost ones, and the graded path
  has no prediction.

### Limits

Binary KPIs are drawn from a 2×2 table with one ρ per cell, and graded ones from one latent
model with σ = 1. KPIs are independent, which makes "every KPI SHIPs" as hard as it gets;
correlated KPIs would SHIP more often. Not simulated: samples lost inside a K > 1 case, missing
pairs with graded scores or several KPIs, Benjamini-Hochberg for advisory KPIs, flag bands and
an unsure judge. Tango's and the SCAS-style bound are comparators here; the gate reads BCa.

## Rerun

```bash
uv run python -m lgcov.gate_sim 2000 0
```

Part 1's cell. The arguments are runs per arm and the base seed. It takes about 70 seconds.

```bash
uv run python -m lgcov.gate_sim grid 5000 0 --scenario-reps 1000 --workers 6
```

Part 2. It logs every skip with its reason, then prints one table per scenario and the
lower-bound table. On 17 Nov 2026 it took 2 h 56 min on 6 workers, on a laptop shared with
other jobs: the graded scenario took a third of that, and the 4-KPI one a fifth. The worker
count changes the time, not the numbers.
