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
    ni_required_n,
    no_drop_bound,
    no_drop_cases,
    paired_test,
    required_n,
    rho_from_2x2,
    rho_from_counts,
    sigma_d,
    sign_flip_p,
    wilson,
)


def test_identical_paired_scores_on_five_cases_are_p_one_delta_zero_and_inconclusive() -> None:
    same = [[1.0], [0.0], [1.0], [1.0], [0.0]]
    r = paired_test(same, same)
    assert (r.p, r.delta, r.ci, r.discordant) == (1.0, 0.0, (0.0, 0.0), 0)
    # T150: no discordant pair is evidence, but 5 cases leave room for a drop of 0.74
    assert decide(r.delta, r.ci, discordant=r.discordant, n=r.n) == "INCONCLUSIVE"


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


# T150: the appendix's inputs (p 0.80; rho 0 / 0.3 / 0.5 / 0.7; delta 0.05; alpha 0.05 two-sided;
# power 0.8). Sample sizes round up; the appendix rounds to the nearest case (547, 703, 502, 301).
RHOS = (0.0, 0.3, 0.5, 0.7)


def test_detection_n_is_the_cases_to_find_a_true_five_point_drop() -> None:
    assert [required_n(0.80, 0.75, rho) for rho in RHOS] == [1091, 765, 548, 330]


def test_a_block_among_three_kpis_needs_more_cases_than_one_alone() -> None:
    # T215: Holm's strictest step tests at alpha / K_gate, so N* is sized there
    alone = required_n(0.80, 0.75, 0.3)
    among_three = required_n(0.80, 0.75, 0.3, alpha=0.05 / 3)
    assert (alone, among_three) == (765, 1021)


def test_non_inferiority_n_is_the_cases_to_rule_out_a_drop_of_the_margin() -> None:
    assert [ni_required_n(0.80, 0.80, rho, 0.05) for rho in RHOS] == [1005, 704, 503, 302]
    assert ni_required_n(0.80, 0.75, 0.3, 0.05) is None  # the expected drop is the margin


def test_mde_is_two_sided_like_the_95_percent_ci_decide_reads() -> None:
    sd = sigma_d(0.80, 0.80, 0.3)
    assert [round(mde(sd, n), 3) for n in (50, 100, 200, 400)] == [0.188, 0.133, 0.094, 0.066]


def test_identical_sides_ship_once_the_no_drop_bound_is_under_the_margin() -> None:
    same = [[1.0]] * 80 + [[0.0]] * 20  # n = 100 and no discordant pair
    r = paired_test(same, same)
    assert (r.n, r.discordant, r.ci) == (100, 0, (0.0, 0.0))
    assert no_drop_bound(100) == pytest.approx(-math.log(0.025) / 100)  # about 0.037
    assert decide(r.delta, r.ci, 0.05, discordant=r.discordant, n=r.n) == "SHIP"


def test_identical_sides_under_74_cases_are_inconclusive() -> None:
    same = [[1.0]] * 40 + [[0.0]] * 10  # n = 50: the drop could still be up to 0.074
    r = paired_test(same, same)
    assert decide(r.delta, r.ci, 0.05, discordant=r.discordant, n=r.n) == "INCONCLUSIVE"
    assert no_drop_cases(0.05) == 74


def test_a_graded_kpi_scales_the_no_drop_bound_by_its_score_range() -> None:
    assert no_drop_bound(50, scale=0.5) == pytest.approx(no_drop_bound(50) / 2)
    assert no_drop_cases(0.05, scale=0.5) == 37
    assert decide(0.0, (0.0, 0.0), 0.05, discordant=0, n=50, scale=0.5) == "SHIP"


@pytest.mark.parametrize(
    ("delta", "ci"),
    [
        (math.nan, (-0.01, 0.02)),  # a CI that alone would SHIP
        (0.0, (-0.01, math.nan)),  # a valid low end and a NaN high end: SHIP before T150
        (math.nan, (math.nan, math.nan)),
    ],
)
def test_a_nan_anywhere_is_inconclusive(delta: float, ci: tuple[float, float]) -> None:
    assert decide(delta, ci, 0.05) == "INCONCLUSIVE"


def test_a_zero_width_ci_with_a_nonzero_delta_is_inconclusive() -> None:
    r = paired_test([[0.5]] * 30, [[0.6]] * 30)  # every case up 0.1: the bootstrap CI collapses
    assert r.ci == pytest.approx((0.1, 0.1)) and r.discordant == 30
    significant = r.p <= 0.05
    assert decide(r.delta, r.ci, 0.05, significant, discordant=r.discordant, n=r.n) == (
        "INCONCLUSIVE"
    )


def test_a_consistent_significant_drop_still_blocks() -> None:
    r = paired_test([[1.0]] * 20, [[0.0]] * 20)  # every case fails on the PR
    assert r.ci == (-1.0, -1.0) and r.p < 0.05 and r.discordant == 20
    assert decide(r.delta, r.ci, 0.05, True, discordant=r.discordant, n=r.n) == "BLOCK"


# T115: rho is the base-vs-candidate correlation from their 2x2 table of per-case pass/fail.
def _table(n11: int, n10: int, n01: int, n00: int) -> tuple[list[list[float]], list[list[float]]]:
    """Paired binary cases with these cells: both pass, base only, candidate only, both fail."""
    base = [[1.0]] * (n11 + n10) + [[0.0]] * (n01 + n00)
    new = [[1.0]] * n11 + [[0.0]] * n10 + [[1.0]] * n01 + [[0.0]] * n00
    return base, new


def test_rho_comes_from_the_2x2_table() -> None:
    assert rho_from_2x2(0.80, 0.78, 0.70) == pytest.approx(0.459, abs=0.001)
    assert rho_from_counts(70, 10, 8, 12) == pytest.approx(rho_from_2x2(0.80, 0.78, 0.70))


@pytest.mark.parametrize(
    ("p_base", "p_cand", "p11"), [(1.0, 0.78, 0.78), (0.80, 0.0, 0.0), (0.0, 1.0, 0.0)]
)
def test_rho_is_undefined_not_a_crash_when_a_side_never_varies(
    p_base: float, p_cand: float, p11: float
) -> None:
    assert rho_from_2x2(p_base, p_cand, p11) is None


def test_an_empty_table_has_no_rho() -> None:
    assert rho_from_counts(0, 0, 0, 0) is None


def test_an_impossible_table_is_refused_with_a_float_tolerance() -> None:
    # p11 lies in [max(0, p_base + p_cand - 1), min(p_base, p_cand)] = [0.58, 0.78] here
    with pytest.raises(ValueError, match="impossible 2x2 table"):
        rho_from_2x2(0.80, 0.78, 0.79)  # more pass on both sides than pass on the candidate
    assert rho_from_2x2(0.80, 0.78, 0.78 + 1e-12) is not None  # a float on the boundary
    assert rho_from_2x2(0.80, 0.78, 0.58 - 1e-12) is not None
    for p11 in (0.78 + 1e-6, 0.58 - 1e-6):
        with pytest.raises(ValueError):
            rho_from_2x2(0.80, 0.78, p11)
    with pytest.raises(ValueError):
        rho_from_2x2(1.2, 0.5, 0.5)


def test_paired_test_takes_rho_from_the_2x2_table() -> None:
    r = paired_test(*_table(70, 10, 8, 12))
    assert r.rho is not None and r.rho == pytest.approx(0.459, abs=0.001)
    assert r.sd == pytest.approx(sigma_d(0.80, 0.78, r.rho))


def test_a_side_that_never_varies_has_no_rho_and_the_gate_still_blocks() -> None:
    r = paired_test(*_table(180, 20, 0, 0))  # base passes all 200; the PR fails 20
    assert r.rho is None
    assert r.sd == pytest.approx(0.3)  # rho drops out of sigma_d
    assert r.required_n == 71
    assert decide(r.delta, r.ci, 0.05, r.p <= 0.05, discordant=r.discordant, n=r.n) == "BLOCK"
