"""T091: paired significance for the L2 gate. Pure: numbers in, numbers out.

The unit is a case: the same input judged on base and on the PR. With K > 1 samples per
case, each side is averaged within the case first, and d_i = s_PR,i - s_base,i.

- Binary KPIs with K = 1: exact McNemar on the discordant pairs.
- Graded scores or K > 1: paired sign-flip permutation (exact for n <= 20, else seeded
  Monte Carlo), plus a seeded BCa bootstrap 95% CI on the mean difference.
- Per-KPI decision against a non-inferiority margin; Holm or Benjamini-Hochberg across
  KPIs. T150: the detection N (Kotawala 2026, Eq. 6, McNemar-Connor) and the
  non-inferiority N are different numbers; identical sides are judged by a no-drop bound;
  the MDE is two-sided, like the 95% CI decide() reads.

Design credit: the sign-flip p-value's (extreme + 1) / (B + 1) form and the BCa interval
(tie-aware bias correction, jackknife acceleration, fallback when the correction
collapses) follow statgate by Yashwanth Chimata (MIT; see NOTICE). This module is a
dependency-free reimplementation, not a copy.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist, fmean, stdev
from typing import Literal

Decision = Literal["SHIP", "BLOCK", "INCONCLUSIVE"]
_N = NormalDist()
_EPS = 1e-12
_Z95 = _N.inv_cdf(0.975)


def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact McNemar: b = base pass & PR fail, c = the reverse."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1))
    return min(1.0, tail / (1 << (n - 1)))  # 2 * tail / 2**n, in exact integers


def sign_flip_p(
    diffs: Sequence[float], flips: int = 10_000, seed: int = 0, exact_max: int = 20
) -> float:
    """Two-sided paired sign-flip test: the share of sign flips at least as extreme."""
    if not diffs:
        raise ValueError("sign_flip_p needs at least one difference")
    if max(abs(d) for d in diffs) < _EPS:
        return 1.0
    observed = abs(sum(diffs))  # same n on every flip, so sums order like means
    tol = 1e-9 * max(1.0, observed)
    if len(diffs) <= exact_max:
        sums = [0.0]
        for d in diffs:
            sums = [s + d for s in sums] + [s - d for s in sums]
        return sum(1 for s in sums if abs(s) >= observed - tol) / len(sums)
    rng = random.Random(seed)
    extreme = 0
    for _ in range(flips):
        if abs(sum(d if rng.random() < 0.5 else -d for d in diffs)) >= observed - tol:
            extreme += 1
    return (extreme + 1) / (flips + 1)  # the observed flip counts too: never exactly 0


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def bca_ci(
    diffs: Sequence[float], level: float = 0.95, resamples: int = 2000, seed: int = 0
) -> tuple[float, float]:
    """Bias-corrected and accelerated bootstrap CI for the mean of the differences."""
    mean = fmean(diffs)
    if len(diffs) < 2 or max(diffs) - min(diffs) < _EPS:
        return (mean, mean)
    rng = random.Random(seed)
    n = len(diffs)
    boot = sorted(fmean(rng.choices(diffs, k=n)) for _ in range(resamples))
    below = sum(1 for b in boot if b < mean)
    ties = sum(1 for b in boot if b == mean)
    p0 = min(
        max((below + 0.5 * ties) / resamples, 1 / (resamples + 1)), resamples / (resamples + 1)
    )
    z0 = _N.inv_cdf(p0)
    total = sum(diffs)
    jack = [(total - d) / (n - 1) for d in diffs]
    jbar = fmean(jack)
    spread = 6 * sum((jbar - j) ** 2 for j in jack) ** 1.5
    accel = sum((jbar - j) ** 3 for j in jack) / spread if spread > _EPS else 0.0
    alpha = 1 - level
    qs = []
    for z in (_N.inv_cdf(alpha / 2), _N.inv_cdf(1 - alpha / 2)):
        correction = 1 - accel * (z0 + z)
        qs.append(_N.cdf(z) if correction <= _EPS else _N.cdf(z0 + (z0 + z) / correction))
    lo, hi = _quantile(boot, qs[0]), _quantile(boot, qs[1])
    return (min(lo, hi), max(lo, hi))


def wilson(successes: int, n: int, z: float = _Z95) -> tuple[float, float]:
    """Wilson score interval for a rate; sound at small n, unlike the normal one."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    lo = 0.0 if successes == 0 else max(0.0, centre - half)
    hi = 1.0 if successes == n else min(1.0, centre + half)
    return (lo, hi)


def sigma_d(p_a: float, p_b: float, rho: float) -> float:
    """SD of a paired binary difference (Kotawala 2026, Eq. 6). rho is the base-vs-candidate
    correlation from their 2x2 table (rho_from_2x2), never one from two baseline runs. When a
    side never varies, the rho term is 0 whatever rho is."""
    qa, qb = 1 - p_a, 1 - p_b
    var = p_a * qa + p_b * qb - 2 * rho * math.sqrt(p_a * qa * p_b * qb)
    return math.sqrt(max(var, 0.0))


_TOL = 1e-9  # T115: a float on a 2x2 table's boundary is on it


def rho_from_2x2(p_base: float, p_cand: float, p11: float) -> float | None:
    """T115: the correlation of paired pass/fail outcomes from the base-vs-candidate 2x2
    table, p11 being the share of cases that pass on both sides. None when a side never
    varies (p = 0 or 1): rho is undefined there, and sigma_d doesn't need it.
    """
    low, high = max(0.0, p_base + p_cand - 1), min(p_base, p_cand)
    in_range = all(-_TOL <= p <= 1 + _TOL for p in (p_base, p_cand, p11))
    if not in_range or not low - _TOL <= p11 <= high + _TOL:
        raise ValueError(
            f"impossible 2x2 table: p_base={p_base}, p_cand={p_cand}, p11={p11} "
            f"(p11 must be in [{low:g}, {high:g}])"
        )
    spread = p_base * (1 - p_base) * p_cand * (1 - p_cand)
    if spread < _EPS:
        return None
    return max(-1.0, min(1.0, (p11 - p_base * p_cand) / math.sqrt(spread)))


def rho_from_counts(n11: int, n10: int, n01: int, n00: int) -> float | None:
    """T115: rho_from_2x2 from the four cells: both pass, base only, candidate only, both
    fail. None for an empty table."""
    if min(n11, n10, n01, n00) < 0:
        raise ValueError(f"2x2 counts can't be negative: {(n11, n10, n01, n00)}")
    n = n11 + n10 + n01 + n00
    if n == 0:
        return None
    return rho_from_2x2((n11 + n10) / n, (n11 + n01) / n, n11 / n)


def _required(sd: float, effect: float, alpha: float, power: float) -> int | None:
    if abs(effect) < _EPS:
        return None  # no effect to detect: no finite N
    z = _N.inv_cdf(1 - alpha / 2) + _N.inv_cdf(power)
    return math.ceil(z * z * sd * sd / (effect * effect))


def cases_needed(sd: float, effect: float, alpha: float = 0.05, power: float = 0.8) -> int | None:
    """T090: paired cases needed to detect `effect` at alpha / power, given the SD of the
    per-case difference; None for no effect. Detection only (T150): it sizes the thin rule,
    whether a group has the cases to BLOCK; it is not the non-inferiority N."""
    return _required(sd, effect, alpha, power)


def required_n(
    p_a: float, p_b: float, rho: float, alpha: float = 0.05, power: float = 0.8
) -> int | None:
    """Cases needed to detect p_A - p_B with paired outcomes (Kotawala 2026, Eq. 6,
    McNemar-Connor), with rho the correlation of the paired outcomes from the base-vs-candidate
    2x2 table (rho_from_2x2, T115), never from two baseline runs. Not Cohen's h x (1 - rho),
    which understates N by about half.

    DETECTION only (superiority, T150): the cases to find a true difference. It sizes N* for
    an INCONCLUSIVE KPI's observed delta. It is never the non-inferiority (harm) rule's N, the
    cases SHIP needs to rule out a drop of the margin: that is ni_required_n.
    """
    return _required(sigma_d(p_a, p_b, rho), p_a - p_b, alpha, power)


def ni_required_n(
    p_a: float, p_b: float, rho: float, margin: float, alpha: float = 0.05, power: float = 0.8
) -> int | None:
    """T150: cases for the non-inferiority test, enough for the 95% CI's low end to clear
    -margin when the true rates are p_A (base) and p_B (PR): what SHIP needs. Two-sided z,
    since decide() reads the low end of a two-sided 95% CI (a one-sided test at alpha / 2).
    None when the expected drop reaches the margin: then no N shows non-inferiority. rho as
    in required_n: from the base-vs-candidate 2x2 table.
    """
    room = p_b - p_a + margin
    if room <= _EPS:
        return None
    return _required(sigma_d(p_a, p_b, rho), room, alpha, power)


def mde(sd: float, n: int, alpha: float = 0.05, power: float = 0.8) -> float:
    """Two-sided minimum detectable effect for n paired cases (T150): the z of the 95% CI
    decide() reads, so it is the smallest drop this n can show."""
    return (_N.inv_cdf(1 - alpha / 2) + _N.inv_cdf(power)) * sd / math.sqrt(n)


def no_drop_bound(n: int, alpha: float = 0.05, scale: float = 1.0) -> float:
    """T150: with no discordant pair among n cases, the one-sided upper bound on the drop
    at the end of the 95% CI decide() reads: -ln(alpha / 2) / n, about 3.69 / n (a little
    above the exact Clopper-Pearson 1 - (alpha / 2)^(1/n)), times the KPI's score range for
    graded scores."""
    return -math.log(alpha / 2) * scale / n


def no_drop_cases(margin: float, alpha: float = 0.05, scale: float = 1.0) -> int:
    """T150: the fewest identical cases whose no-drop bound is under the margin: 74 at 0.05."""
    return math.floor(-math.log(alpha / 2) * scale / margin) + 1


def _order(pvalues: Sequence[float]) -> list[int]:
    return sorted(range(len(pvalues)), key=lambda i: pvalues[i])


def holm(pvalues: Sequence[float], alpha: float = 0.05) -> list[bool]:
    """Holm step-down: reject p_(j) <= alpha / (m - j + 1) until the first that isn't."""
    m, reject = len(pvalues), [False] * len(pvalues)
    for rank, i in enumerate(_order(pvalues)):
        if pvalues[i] > alpha / (m - rank):
            break
        reject[i] = True
    return reject


def holm_adjusted(pvalues: Sequence[float]) -> list[float]:
    m, adjusted, running = len(pvalues), [1.0] * len(pvalues), 0.0
    for rank, i in enumerate(_order(pvalues)):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def benjamini_hochberg(pvalues: Sequence[float], alpha: float = 0.05) -> list[bool]:
    """BH step-up: reject the k smallest, for the largest k with p_(k) <= k * alpha / m."""
    m, order = len(pvalues), _order(pvalues)
    k = max((r + 1 for r, i in enumerate(order) if pvalues[i] <= (r + 1) * alpha / m), default=0)
    reject = [False] * m
    for i in order[:k]:
        reject[i] = True
    return reject


def benjamini_hochberg_adjusted(pvalues: Sequence[float]) -> list[float]:
    m, adjusted, running = len(pvalues), [1.0] * len(pvalues), 1.0
    for rank, i in reversed(list(enumerate(_order(pvalues)))):
        running = min(running, m * pvalues[i] / (rank + 1))
        adjusted[i] = running
    return adjusted


def decide(
    delta: float,
    ci: tuple[float, float],
    margin: float = 0.05,
    significant: bool = True,
    *,
    discordant: int | None = None,
    n: int = 0,
    scale: float = 1.0,
    alpha: float = 0.05,
) -> Decision:
    """BLOCK: the whole CI is below 0, the drop is at least the margin, and the
    (multiplicity-adjusted) test rejects. SHIP: the CI's low end is above -margin.

    T150: a NaN anywhere is INCONCLUSIVE. No discordant pair among n cases is evidence of no
    drop: SHIP once no_drop_bound(n) is under the margin, INCONCLUSIVE before. Any other
    zero-width CI (every case moved the same way) can't SHIP: it says nothing about spread.
    """
    lo, hi = ci
    if math.isnan(delta) or math.isnan(lo) or math.isnan(hi):
        return "INCONCLUSIVE"
    if discordant == 0:
        return "SHIP" if n > 0 and no_drop_bound(n, alpha, scale) < margin else "INCONCLUSIVE"
    if hi < 0 and delta <= -margin and significant:
        return "BLOCK"
    if hi - lo < _EPS:
        return "INCONCLUSIVE"
    if lo > -margin:
        return "SHIP"
    return "INCONCLUSIVE"


@dataclass(frozen=True)
class PairedResult:
    n: int
    base_rate: float
    new_rate: float
    delta: float  # PR minus base
    ci: tuple[float, float]  # 95% BCa CI on delta
    p: float  # two-sided, before any multiplicity adjustment
    method: Literal["mcnemar", "sign-flip"]
    rho: float | None  # T115: binary, from the base-vs-PR 2x2 table, None when a side never
    # varies; graded, the Pearson correlation of per-case means, reported only
    sd: float  # SD of the per-case difference (Eq. 6 for binary KPIs)
    required_n: int | None  # to detect delta at alpha / power; None if delta is 0
    mde: float  # two-sided minimum detectable effect at this n (T150)
    discordant: int  # T150: cases that differ between the sides (b + c for binary KPIs)
    base_rate_ci: tuple[float, float] | None = None  # Wilson, binary KPIs only
    new_rate_ci: tuple[float, float] | None = None


def _correlation(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mx, my = fmean(xs), fmean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx < _EPS or sy < _EPS:
        return 0.0  # a side that never varies carries no correlation
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (sx * sy)


def paired_test(
    base: Sequence[Sequence[float]],
    new: Sequence[Sequence[float]],
    alpha: float = 0.05,
    power: float = 0.8,
    seed: int = 0,
) -> PairedResult:
    """Paired test of per-case scores in [0, 1]: base[i] and new[i] are one case's samples."""
    if len(base) != len(new) or not base or not all(base) or not all(new):
        raise ValueError("paired_test needs the same non-empty cases on both sides to pair")
    binary = all(len(s) == 1 and s[0] in (0.0, 1.0) for s in (*base, *new))
    b_means, n_means = [fmean(s) for s in base], [fmean(s) for s in new]
    diffs = [n - b for n, b in zip(n_means, b_means, strict=True)]
    n, p_a, p_b = len(diffs), fmean(b_means), fmean(n_means)
    method: Literal["mcnemar", "sign-flip"]
    rate_cis: tuple[tuple[float, float] | None, tuple[float, float] | None]
    rho: float | None
    if binary:
        pairs = list(zip(b_means, n_means, strict=True))
        b = sum(1 for x, y in pairs if x == 1.0 and y == 0.0)
        c = sum(1 for x, y in pairs if x == 0.0 and y == 1.0)
        both = sum(1 for x, y in pairs if x == 1.0 and y == 1.0)
        rho = rho_from_counts(both, b, c, n - both - b - c)  # T115: the base-vs-PR 2x2 table
        sd = sigma_d(p_a, p_b, 0.0 if rho is None else rho)  # undefined rho: no rho term
        p, method = mcnemar_exact_p(b, c), "mcnemar"
        rate_cis = (wilson(round(p_a * n), n), wilson(round(p_b * n), n))
        discordant = b + c
    else:
        rho = _correlation(b_means, n_means)  # graded: no 2x2 table; sd comes from the diffs
        p, method = sign_flip_p(diffs, seed=seed), "sign-flip"
        sd = stdev(diffs) if n > 1 else 0.0
        rate_cis = (None, None)
        discordant = sum(1 for d in diffs if abs(d) > _EPS)
    delta = fmean(diffs)
    return PairedResult(
        n=n,
        base_rate=p_a,
        new_rate=p_b,
        delta=delta,
        ci=bca_ci(diffs, seed=seed),
        p=p,
        method=method,
        rho=rho,
        sd=sd,
        required_n=_required(sd, delta, alpha, power),
        mde=mde(sd, n, alpha, power),
        discordant=discordant,
        base_rate_ci=rate_cis[0],
        new_rate_ci=rate_cis[1],
    )
