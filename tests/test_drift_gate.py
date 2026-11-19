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
        ("harmed-root", 2, 0, 0),  # T262: without a harmed count, do not fail
        ("harmed-root", 0, 0, 0),
    ],
)
def test_drift_exit_code(fail_on: str, root: int, propagated: int, code: int) -> None:
    assert drift_exit_code(parse_fail_on(fail_on), root, propagated) == code


def test_harmed_root_fails_only_when_harmed_count_is_set() -> None:
    assert drift_exit_code("harmed-root", 3, 0, harmed=1) == 1
    assert drift_exit_code("harmed-root", 3, 0, harmed=0) == 0


def test_fail_on_typo_lists_the_allowed_values() -> None:
    assert FAIL_ON_VALUES == ("root-drift", "any-drift", "never", "harmed-root")
    assert FAIL_ON_VALUES[0] == "root-drift"  # default stays root-drift
    with pytest.raises(
        ValueError, match=r"root-drift, any-drift, never, harmed-root; got 'root-drif'"
    ):
        parse_fail_on("root-drif")


@pytest.mark.parametrize(("code", "expected"), [(0, "pass"), (1, "fail"), (2, "error")])
def test_verdict_names_the_exit_code(code: int, expected: str) -> None:
    assert verdict(code) == expected
