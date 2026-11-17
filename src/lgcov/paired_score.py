"""T151 part 2: score intervals for a paired difference of pass rates, Δ = p_PR - p_base.

Comparators for the gate simulation (gate_sim). The gate itself reads a BCa interval; these
decide nothing in lgcov. Written from the papers:

- tango_ci: Tango's asymptotic score interval (T. Tango, "Equivalence test and confidence
  interval for the difference in proportions for the paired-sample design", Statistics in
  Medicine 17:891-908, 1998). The score is Δ̂ - θ over its standard error under the cell
  probabilities that maximise the likelihood given Δ = θ, which have a closed form.
- skew_corrected_ci: SCAS-style, derived here. P. J. Laud, "Equal-tailed confidence intervals
  for comparison of rates" (Pharmaceutical Statistics 16:334-348, 2017; corrigendum 17:290-293,
  2018), corrects the score for skewness (Eq. 2) and multiplies its variance by N / (N - 1)
  (Appendix A.1), for two independent samples. Here both are applied to Tango's paired score,
  with the paired third central moment derived from its definition below.

The limits are the two θ where the score equals ±z, found by bisection, as Laud describes.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from statistics import NormalDist

_STEPS = 60  # bisection halvings: the bracket ends narrower than 1e-17


def _score(
    base_only: int, pr_only: int, n: int, theta: float, z: float, skew: bool, bcf: bool
) -> float:
    """The score at θ. A case's difference D is +1 (only the PR passes), -1 (only the base
    passes) or 0. With q = P(D = -1) and P(D = +1) = q + θ, the likelihood's maximum given θ
    solves 2n q² + b q + c = 0 (Tango 1998). Then Var(D) = 2q + θ(1 - θ), and
    E[(D - θ)³] = E[D³] - 3θ E[D²] + 2θ³ = θ - 3θ(2q + θ) + 2θ³, since E[D³] = θ."""
    b = -(pr_only + base_only) + (2 * n - pr_only + base_only) * theta
    c = -base_only * theta * (1 - theta)
    q = max(0.0, (math.sqrt(max(b * b - 8 * n * c, 0.0)) - b) / (4 * n))
    var = (2 * q + theta * (1 - theta)) / n * (n / (n - 1) if bcf else 1.0)
    s = (pr_only - base_only) / n - theta
    if var <= 0:
        return math.copysign(math.inf, s) if s else 0.0
    out = s / math.sqrt(var)
    if skew:  # Laud's Eq. 2: S / V^½ - (z² - 1) μ3 / (6 V^(3/2))
        mu3 = (theta - 3 * theta * (2 * q + theta) + 2 * theta**3) / n**2
        out -= (z * z - 1) * mu3 / (6 * var**1.5)
    return out


def _root(f: Callable[[float], float], lo: float, hi: float) -> float:
    """Where a decreasing f crosses 0 between lo and hi."""
    for _ in range(_STEPS):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) > 0 else (lo, mid)
    return (lo + hi) / 2


def score_ci(
    both: int,
    base_only: int,
    pr_only: int,
    neither: int,
    *,
    skew: bool = False,
    bcf: bool = False,
    level: float = 0.95,
) -> tuple[float, float]:
    """The score interval for Δ from a paired 2x2 table: both pass, base only, PR only, both
    fail. skew adds Laud's skewness correction, bcf his N / (N - 1) variance factor."""
    n = both + base_only + pr_only + neither
    if n < 2:
        raise ValueError(f"a paired score interval needs at least 2 cases, got {n}")
    z = NormalDist().inv_cdf(0.5 + level / 2)
    est = (pr_only - base_only) / n

    def score(theta: float) -> float:
        return _score(base_only, pr_only, n, theta, z, skew, bcf)

    return (_root(lambda t: score(t) - z, -1.0, est), _root(lambda t: score(t) + z, est, 1.0))


def tango_ci(
    both: int, base_only: int, pr_only: int, neither: int, level: float = 0.95
) -> tuple[float, float]:
    """Tango's asymptotic score interval for Δ (Tango 1998)."""
    return score_ci(both, base_only, pr_only, neither, level=level)


def skew_corrected_ci(
    both: int, base_only: int, pr_only: int, neither: int, level: float = 0.95
) -> tuple[float, float]:
    """The SCAS-style interval for Δ, derived here: Tango's score with Laud's (2017) skewness
    correction and N / (N - 1) variance factor."""
    return score_ci(both, base_only, pr_only, neither, skew=True, bcf=True, level=level)
