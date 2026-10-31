"""T091: paired significance statistics (pure)."""

import itertools
import math
from statistics import fmean

import pytest

from lgcov.stats import (
    bca_ci,
    benjamini_hochberg,
    benjamini_hochberg_adjusted,
    decide,
    holm,
    holm_adjusted,
    mcnemar_exact_p,
    mde,
    paired_test,
    required_n,
    sigma_d,
    sign_flip_p,
    wilson,
)


def test_identical_paired_scores_are_p_one_delta_zero_and_ship() -> None:
    same = [[1.0], [0.0], [1.0], [1.0], [0.0]]
    r = paired_test(same, same)
    assert (r.p, r.delta, r.ci) == (1.0, 0.0, (0.0, 0.0))
    assert decide(r.delta, r.ci) == "SHIP"


def test_kotawala_worked_example_and_not_the_shortcut() -> None:
    # Kotawala 2026 Eq. 6 (McNemar–Connor): p_A=0.65, p_B=0.60, rho=0.30 -> N* ~ 1,028.
    n = required_n(0.65, 0.60, 0.30)
    assert n is not None and 1027 <= n <= 1029
    assert not 480 <= n <= 540  # Cohen's h x (1 - rho) would say ~515


def test_mcnemar_exact_b0_c8() -> None:
    assert mcnemar_exact_p(0, 8) == pytest.approx(0.0078125)
    assert mcnemar_exact_p(0, 0) == 1.0
    assert mcnemar_exact_p(3, 3) == 1.0  # capped


def test_sign_flip_exact_matches_brute_force_over_all_32_flips() -> None:
    diffs = [0.5, -0.25, 1.0, 0.75, -0.5]
    observed = abs(fmean(diffs))
    flips = list(itertools.product((1, -1), repeat=5))
    brute = sum(abs(fmean(s * d for s, d in zip(f, diffs, strict=True))) >= observed for f in flips)
    assert sign_flip_p(diffs) == pytest.approx(brute / 32)


def test_sign_flip_monte_carlo_is_seeded_and_never_zero() -> None:
    diffs = [-1.0] * 25  # n > 20: 10,000 seeded flips
    p = sign_flip_p(diffs, seed=7)
    assert p == sign_flip_p(diffs, seed=7)
    assert 0 < p <= 2 / 10_001


def test_bca_ci_contains_delta() -> None:
    diffs = [0.0, -1.0, 0.0, 0.0, -1.0, 0.5, 0.0, -0.5, 0.0, -1.0, 0.0, 0.25]
    lo, hi = bca_ci(diffs, seed=1)
    assert lo <= fmean(diffs) <= hi
    assert lo < hi


def test_holm_rejects_only_the_first_of_three() -> None:
    assert holm([0.01, 0.03, 0.04], alpha=0.05) == [True, False, False]
    assert holm_adjusted([0.01, 0.03, 0.04]) == pytest.approx([0.03, 0.06, 0.06])


def test_benjamini_hochberg_step_up() -> None:
    assert benjamini_hochberg([0.01, 0.02, 0.03, 0.04], alpha=0.05) == [True] * 4
    assert benjamini_hochberg([0.04, 0.5], alpha=0.05) == [False, False]


def test_benjamini_hochberg_adjusted_p_values_are_monotone() -> None:
    # T091 PR B: the advisory KPIs' "p adj." column; adjusted <= alpha iff BH rejects.
    assert benjamini_hochberg_adjusted([0.04, 0.5]) == pytest.approx([0.08, 0.5])
    assert benjamini_hochberg_adjusted([0.01, 0.04, 0.03, 0.02]) == pytest.approx([0.04] * 4)


def test_mde_shrinks_with_one_over_sqrt_n() -> None:
    sd = sigma_d(0.65, 0.60, 0.30)
    assert mde(sd, 400) == pytest.approx(mde(sd, 100) / 2)


def test_wilson_stays_inside_zero_one_at_small_n() -> None:
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and 0.25 < hi < 0.31  # a normal interval would give [0, 0]
    assert wilson(10, 10)[1] == 1.0


@pytest.mark.parametrize(
    ("delta", "ci", "significant", "want"),
    [
        (-0.10, (-0.18, -0.03), True, "BLOCK"),
        (-0.10, (-0.18, -0.03), False, "INCONCLUSIVE"),  # multiplicity-adjusted p too big
        (-0.01, (-0.04, 0.02), True, "SHIP"),
        (-0.06, (-0.30, 0.10), True, "INCONCLUSIVE"),
    ],
)
def test_decide_with_a_non_inferiority_margin(
    delta: float, ci: tuple[float, float], significant: bool, want: str
) -> None:
    assert decide(delta, ci, margin=0.05, significant=significant) == want


def test_binary_paired_test_uses_mcnemar_and_reports_power() -> None:
    base = [[1.0]] * 100
    new = [[1.0]] * 90 + [[0.0]] * 10  # 10 discordant pairs, all base-pass / PR-fail
    r = paired_test(base, new)
    assert r.method == "mcnemar"
    assert r.p == pytest.approx(2 * 0.5**10)
    assert r.delta == pytest.approx(-0.10)
    assert r.new_rate_ci is not None and r.new_rate_ci[0] < 0.9 < r.new_rate_ci[1]
    assert r.required_n is not None and r.mde > 0


def test_graded_or_multi_sample_cases_use_the_sign_flip_test() -> None:
    base = [[1.0, 1.0, 0.0]] * 6
    new = [[1.0, 0.0, 0.0]] * 6  # K=3: averaged within each case first
    r = paired_test(base, new)
    assert r.method == "sign-flip"
    assert r.delta == pytest.approx(-1 / 3)
    assert r.p == pytest.approx(2 / 64)  # exact: only the all-+ and all-- flips are as extreme
    assert math.isclose(r.base_rate, 2 / 3) and r.base_rate_ci is None


def test_cases_must_pair_up() -> None:
    with pytest.raises(ValueError, match="pair"):
        paired_test([[1.0]], [[1.0], [0.0]])
