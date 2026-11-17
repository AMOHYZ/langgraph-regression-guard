"""T151 part 2: Tango's and the SCAS-style paired score intervals (pure, offline).

The expected bounds for the table (1, 1, 7, 12) are ratesci's published values (its vignette
"Paired contrasts", rdpairci). They are test vectors only: no ratesci code is used.
"""

from statistics import NormalDist

import pytest

from lgcov.paired_score import score_ci, skew_corrected_ci, tango_ci
from lgcov.stats import no_drop_bound

# ratesci's c(1, 1, 7, 12) is both, first only, second only, neither. With the PR as the first
# side, that is 1 PR-only and 7 base-only cases among 21, so Δ̂ = -6/21.
TABLE = (1, 7, 1, 12)  # both pass, base only, PR only, both fail


@pytest.mark.parametrize(
    ("skew", "bcf", "published"),
    [
        (False, False, (-0.5172, -0.0260)),  # ratesci's AS(Tango)
        (False, True, (-0.5226, -0.0183)),  # AS(N-1)
        (True, False, (-0.5229, -0.0262)),  # SCASu
        (True, True, (-0.5281, -0.0184)),  # SCAS
    ],
)
def test_bounds_match_ratesci_published_values(
    skew: bool, bcf: bool, published: tuple[float, float]
) -> None:
    assert score_ci(*TABLE, skew=skew, bcf=bcf) == pytest.approx(published, abs=5e-5)


def test_the_named_intervals_are_the_plain_and_the_fully_corrected_score() -> None:
    assert tango_ci(*TABLE) == score_ci(*TABLE)
    assert skew_corrected_ci(*TABLE) == score_ci(*TABLE, skew=True, bcf=True)


def test_no_discordant_pair_gives_tango_closed_form_next_to_the_no_drop_bound() -> None:
    z2 = NormalDist().inv_cdf(0.975) ** 2
    lo, hi = tango_ci(95, 0, 0, 5)
    assert (lo, hi) == pytest.approx((-z2 / (100 + z2), z2 / (100 + z2)), abs=1e-9)
    assert -lo == pytest.approx(no_drop_bound(100), abs=5e-4)  # 0.0370 and the gate's 0.0369


def test_swapping_the_sides_mirrors_the_interval() -> None:
    lo, hi = skew_corrected_ci(40, 9, 3, 8)
    assert skew_corrected_ci(40, 3, 9, 8) == pytest.approx((-hi, -lo), abs=1e-9)


def test_the_estimate_is_inside_and_a_wider_level_holds_the_narrower() -> None:
    lo, hi = skew_corrected_ci(*TABLE)
    assert lo < -6 / 21 < hi
    wide_lo, wide_hi = score_ci(*TABLE, skew=True, bcf=True, level=0.99)
    assert wide_lo < lo and hi < wide_hi


def test_one_case_is_too_few() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        tango_ci(1, 0, 0, 0)
