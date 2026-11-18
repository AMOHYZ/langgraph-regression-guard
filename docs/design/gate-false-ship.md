# False SHIPs at small N and with missing results (design, after T151)

**Status: ruled on 18 November 2026** (section 7). Option A is built. The page
weighs fixes for the two places where [the gate simulations](../validation/gate-simulations.md)
measured the gate SHIPping real drops more often than [gate.md](../gate.md)'s 2.5%, and ends
with the questions that are yours to rule on.

**How it was checked.** Read on 18 November 2026 at `76e75fb`: `decide()` and `paired_test()`
in [`stats.py`](../../src/lgcov/stats.py), `kpi_stats()` and `gate_kpis()` in
[`sampling.py`](../../src/lgcov/sampling.py), `gate_findings()` in
[`verdict_actions.py`](../../src/lgcov/verdict_actions.py), and
[`paired_score.py`](../../src/lgcov/paired_score.py). Every rate quoted is from T151 part 2's
run at `636a28f`. A figure marked *(arithmetic)* is worked out here, not simulated.

## 1. What part 2 measured

A wrong SHIP is a SHIP when the PR's true drop is the full margin, m = 0.05. Its nominal rate
is 2.5%, one side of the 95% CI.

- **Small N.** With binary scores, one KPI and every pair, the gate SHIPs 3.66%, 6.06% and
  5.48% of such PRs at N = 50, and 3.22% at N = 100 in one cell (p = 0.80, ρ = 0.7). From
  N = 200 every cell is within 2.5%. Losing 10% of pairs at random is the same failure on fewer
  pairs: 4.8–7.9% at N = 50, 3.6–4.4% at N = 100. The graded path (K = 3) shows it at p = 0.95:
  3.8% and 4.4% at N = 50, 3.5% at N = 100.
- **Missing results.** When half of the PR's failing results are lost and none of its passes,
  a drop of the margin SHIPs 22–30% of the time at N = 50 in the unclamped cells, and 92–99.6% at
  N = 800. A drop of 0.10 SHIPs up to 65%. More cases make it worse, not better.

Two KPIs or four, under Holm, stay within 2.5% at every N: SHIP needs every KPI to pass.

## 2. Why each happens

**Small N: BCa on a few discordant pairs.** For a binary KPI each case's difference is −1, 0
or +1, and only the discordant pairs vary. At N = 50 and p = 0.95 there are a handful of them.
BCa's bootstrap then sees few distinct resamples, and its lower end sits too high: in 12 of the
52 arms the BCa bound lies above the true Δ more often than 2.5%, 11 of them at N ≤ 100.
Tango's score interval, built from the same 2×2 counts with no resampling, did so in none.

**Missing results: a lost case is dropped, whatever lost it.** `kpi_stats()` pairs a case only
when both sides have a valid score. A judge's infra error (`Sample.passed is None`) leaves the
case out, and nothing compares how often each side lost one. `judge_unreliable` speaks only
when more than half of all judgments failed. So when a PR's bad outputs are the ones its judge
fails on, the PR is graded on its good cases alone, and a larger N makes the gate surer of a
rate that is biased up.

## 3. Small N: the options

Costs are part 2's: SHIP at Δ = 0, an unchanged PR, where SHIP is the right call. Within
2.5% means not above it beyond Monte Carlo error, as in the simulations doc.

| Option | Wrong SHIP at Δ = −m | SHIP at Δ = 0, N = 50 | At N = 100 | From N = 200 |
|---|---|---|---|---|
| Today: BCa | 3.66–6.06% at N = 50 | 13.9–32.8% | 16.6–61.6% | — |
| A. Tango's interval for binary KPIs | within 2.5% in every arm | 8.3–12.0% | 16.2–53.8% | within 2.4 points of BCa |
| B. The SCAS-style interval | 2.94% and 3.50% at N = 100 | 8.6–25.0% | 16.5–68.2% | within 2.4 points |
| C. No SHIP below N = 200 | within 2.5% from N = 200 | 0% | 0% | unchanged |
| D. Tango below N = 200, BCa above | as A | as A | as A | as today |

- **A** fixes every binary cell. It costs SHIPs at N = 50, most in part 1's own cell (31.2% to
  8.3%), and little from N = 100 up. BLOCK barely moves: it rests on the exact McNemar test and
  the CI's upper end, and its rate changed by at most 0.12 points in any arm. Tango's interval
  is closed form and deterministic, so it also drops the bootstrap's cost and seed for binary
  KPIs. It is already in the repo, written from Tango (1998), and tested against published
  values.
- **B** SHIPs more at N = 50, but is itself above 2.5% in two arms at N = 100, both at high ρ,
  where most discordant pairs (all of them, in part 1's cell) are regressions. Its paired third moment is our own
  derivation, not a published one.
- **C** turns away every SHIP on a small suite, so it is worse than A in every row. A floor at
  N = 100 would still leave the 3.22% cell.
- **D** gives the same answers as A from N = 200 within Monte Carlo error. All it adds is a
  seam at an arbitrary N, so it isn't worth having.

**Graded KPIs** have no 2×2 table, so Tango doesn't apply. Their excess is smaller (3.5–4.4%,
at p = 0.95 only) and part 2 tried no alternative for them. A studentized bootstrap or a
t-interval on case means are the candidates. Each needs its own simulation arm before it can
be weighed.

**Recommended: A for binary KPIs, with graded KPIs left on BCa** until a simulation shows a
better interval for them. gate.md would then say that the 2.5% is measured for binary KPIs at
every N simulated, and for graded ones from N = 200.

## 4. Missing results: the options

What can be seen from a run is which side lost each case. A case the base scored and the PR
lost is *PR-only*. A case lost on both sides tells nothing about the PR.

- **E. Say it, change nothing.** gate.md names the limit. A PR whose failures fail the judge
  still SHIPs.
- **F. Flag the imbalance.** Count PR-only and base-only lost cases and run a one-sided exact
  sign test on them. When the PR loses significantly more, SHIP becomes INCONCLUSIVE with a
  line saying why. It detects only once the count is large enough: with no base-only losses,
  5 PR-only losses are the fewest that reach p < 0.05. In part 2's informative scenario at
  Δ = −m that is about 6 expected at N = 50 and p = 0.80, but 2.5 at p = 0.95 *(arithmetic)*,
  so it misses most of the small-N harm. It also fires on a PR that merely made the judge
  flakier.
- **G. Grade SHIP on the worst case too.** Score every PR-only lost case as the PR's lowest
  score (a fail for binary KPIs), and SHIP only if both this filled-in test and today's test
  SHIP. BLOCK reads today's data alone, so G can never add a false BLOCK. In part 2's
  informative scenario the lost results *were* failures, so G rebuilds the true data and false
  SHIP falls back to the main grid's rates. That holds by construction, not by measurement.
  Its cost falls on PRs whose judge lost results at random: if each side loses a fraction e of
  results, about e(1 − e) of cases are PR-only, and filling them in as fails moves Δ by about
  −e(1 − e)·p. At e = 2% and p = 0.90 that is −0.018, a third of the margin *(arithmetic)*.
  Part 2 can't price this, because its random arm lost whole pairs, both sides at once.
- **H. Cap SHIP at a share of PR-only losses.** SHIP becomes INCONCLUSIVE when more than, say,
  2% of cases are PR-only. This is simpler than G but blunter: the threshold is arbitrary, and
  it ignores whether the losses could have moved the bound.

**Recommended: G, measured first.** It is the only option that removes the bias rather than
spotting it, it can't add a false BLOCK, and it costs nothing when no result is lost on one
side only. Before it goes in, two more gate_sim arms should run, with no gate change: random
one-sided loss at e = 2% and 5% per side, to price G's lost SHIPs, and the informative arm
again with G applied. Separately, the PR comment should print the PR-only and base-only counts
next to each KPI whatever is chosen. That is F's evidence without F's verdict.

## 5. What a fix would touch

| Piece | Files | Docs that must change with it |
|---|---|---|
| A: Tango for binary KPIs | `paired_test()` in `stats.py`, `paired_score.py`'s docstring (no longer a comparator only), `tests/test_stats.py` | gate.md (the 2.5% bullet), why-us.md line 127 (names BCa), gate-simulations.md (a rerun row) |
| Lost counts in the comment | `KpiStats` and its table in `sampling.py`, `tests/test_sampling.py` | gate.md, verdict-actions.md |
| G's simulation arms | `gate_sim.py`, `tests/test_gate_sim.py` | gate-simulations.md |
| G in the gate | `kpi_stats()` and `gate_kpis()` in `sampling.py`, tests | gate.md, why-us.md and bake-off.md where they name lgcov's limits |

Each row is its own PR, in this order. A changes verdicts on existing PRs at small N, and so
does G, so each needs a CHANGELOG line and belongs in a minor release. G's simulation arms come
before G and change nothing.

## 6. Questions for you

1. **Small N:** take A (Tango's interval for binary KPIs), knowing it cuts SHIPs of unchanged
   PRs at N = 50 from 14–33% to 8–12%? Or C, D or E?
2. **Graded KPIs:** leave them on BCa and say so in gate.md, or simulate a studentized
   bootstrap and a t-interval first?
3. **Missing results:** run G's two simulation arms (no gate change, no paid calls, about 20
   minutes on 6 workers *(estimate: part 2's random-loss arm took 442 s)*), then decide between G, H and E?
4. **The lost counts in the PR comment:** add them now, apart from the rest?
5. **What loses a PR's results in practice:** part 2 assumed a judge that errors on bad
   outputs. Have you seen judge errors cluster on one side in real runs? If they never do, E
   may be enough for now.

## 7. Rulings (18 November 2026)

1. **A, Tango's interval for binary KPIs: yes.** Built: `paired_test()` reads it for a binary
   KPI with two or more cases. The skew-corrected one keeps its name, SCAS-style, derived here,
   and stays a comparator.
2. **Graded KPIs stay on BCa for now.** gate.md says that below N = 200 they aren't
   simulation-checked. Follow-up: a gate_sim arm for a studentized paired bootstrap on the
   graded path.
3. **G: run both arms first.** Adopt G only if the random-loss penalty is at most 0.02 at 2%
   loss per side, with false BLOCK unchanged.
4. **Per-side loss counts in the PR comment: now.** Until G is decided, an interim rule: when
   the PR side lost more results than the base beyond a small tolerance, the KPI never SHIPs
   (needs a human). The tolerance is still to be ruled on.
5. **Judge errors clustering on the PR side:** never seen in real runs so far.
