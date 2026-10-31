"""T078: the Action's fail-on policy maps drift counts to an exit code."""

import pytest

from lgcov.drift_gate import FAIL_ON_VALUES, drift_exit_code, parse_fail_on, verdict


@pytest.mark.parametrize(
    ("fail_on", "root", "propagated", "code"),
    [
        ("never", 2, 3, 0),
        ("root-drift", 0, 3, 0),
        ("root-drift", 1, 0, 1),
        ("any-drift", 0, 1, 1),
        ("any-drift", 0, 0, 0),
    ],
)
def test_drift_exit_code(fail_on: str, root: int, propagated: int, code: int) -> None:
    assert drift_exit_code(parse_fail_on(fail_on), root, propagated) == code


def test_fail_on_typo_lists_the_three_allowed_values() -> None:
    assert FAIL_ON_VALUES == ("root-drift", "any-drift", "never")
    with pytest.raises(ValueError, match=r"root-drift, any-drift, never; got 'root-drif'"):
        parse_fail_on("root-drif")


@pytest.mark.parametrize(("code", "expected"), [(0, "pass"), (1, "fail"), (2, "error")])
def test_verdict_names_the_exit_code(code: int, expected: str) -> None:
    assert verdict(code) == expected
