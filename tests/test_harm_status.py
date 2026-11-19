"""T262: each drift root gets a harm status from lgcov's own KPIs/code checks only.

`classify` stays a drift label. Harm status never reads a synthetic truth or `regressed`.
fail-on: harmed-root fails only on harmed roots; unmeasured is needs_human (never a pass);
no-harm-seen alone is exit 0. root-drift stays the default.
"""

from pathlib import Path

import pytest

from lgcov.ci_report import CiResult
from lgcov.drift_gate import FAIL_ON_VALUES, HarmStatus, parse_fail_on, root_harm_statuses
from lgcov.taxonomy import load_taxonomy
from lgcov.verdict_actions import CodeFailure, GateFindings

ROOT = Path(__file__).resolve().parent
TAXONOMY = ROOT / "fixtures" / "harm_status" / "taxonomy.yaml"


def _specs() -> dict[str, str]:
    """KPI name -> node this fixture's code check covers."""
    return {s.name: s.node for s in load_taxonomy(TAXONOMY).kpi_specs}


def test_fixture_taxonomy_covers_respond_only() -> None:
    assert _specs() == {"respond_runs": "respond"}


def test_three_roots_one_harmed_one_no_harm_one_unmeasured() -> None:
    """Small fixture: real code-check KPI on respond; lookup_order has none; classify has none.

    - respond + code check newly failed → harmed
    - respond + check ran, no failure → no harm seen
    - lookup_order with no covering KPI → unmeasured
    """
    covered = _specs()
    harmed = root_harm_statuses(
        ("respond", "lookup_order"),
        covered,
        failed_kpis=frozenset({"respond_runs"}),
        judged_kpis=frozenset({"respond_runs"}),
    )
    assert harmed == {
        "respond": "harmed",
        "lookup_order": "unmeasured",
    }
    clear = root_harm_statuses(
        ("respond", "classify"),
        covered,
        failed_kpis=frozenset(),
        judged_kpis=frozenset({"respond_runs"}),
    )
    assert clear == {
        "respond": "no harm seen",
        "classify": "unmeasured",
    }


def test_harm_status_never_takes_a_truth_label() -> None:
    """The API has no place for regressed/benign; only KPI names and nodes."""
    import inspect

    params = inspect.signature(root_harm_statuses).parameters
    assert "regressed" not in params and "truth" not in params and "label" not in params


@pytest.mark.parametrize(
    ("statuses", "verdict", "exit_code", "state"),
    [
        ({"respond": "harmed"}, "fail", 1, "policy_drift"),
        ({"respond": "no harm seen"}, "pass", 0, "no_drift_no_kpi"),
        ({"lookup_order": "unmeasured"}, "needs_human", 0, "unmeasured_root"),
        (
            {"respond": "no harm seen", "lookup_order": "unmeasured"},
            "needs_human",
            0,
            "unmeasured_root",
        ),
    ],
)
def test_harmed_root_fail_on(
    statuses: dict[str, HarmStatus], verdict: str, exit_code: int, state: str
) -> None:
    roots = tuple(statuses)
    result = CiResult(
        "harmed-root",
        root_drift=roots,
        harm_status=statuses,
        gate=GateFindings(),
    )
    assert result.action.verdict == verdict
    assert result.action.exit_code == exit_code
    assert result.action.state == state


def test_root_drift_stays_default_and_still_fails_on_any_root() -> None:
    assert FAIL_ON_VALUES[0] == "root-drift"
    assert parse_fail_on("root-drift") == "root-drift"
    drifted = CiResult("root-drift", root_drift=("lookup_order",))
    assert drifted.action.verdict == "fail"
    assert drifted.action.exit_code == 1
    # Same unmeasured root under harmed-root is needs_human, not fail.
    under = CiResult(
        "harmed-root",
        root_drift=("lookup_order",),
        harm_status={"lookup_order": "unmeasured"},
    )
    assert under.action.verdict == "needs_human"
    assert under.action.exit_code == 0


def test_unmeasured_is_never_a_pass_even_with_a_ship() -> None:
    result = CiResult(
        "harmed-root",
        root_drift=("lookup_order",),
        harm_status={"lookup_order": "unmeasured"},
        gate=GateFindings(statistical="SHIP", k_gate=1),
    )
    assert result.action.verdict == "needs_human"
    assert result.action.exit_code == 0
    assert result.action.state == "unmeasured_root"


def test_code_check_failure_on_a_covered_root_is_still_a_policy_fail() -> None:
    """Blocking code checks still fail the PR on their own; harm status agrees."""
    failure = CodeFailure("respond_runs", ("run-0/respond",), 1)
    result = CiResult(
        "harmed-root",
        root_drift=("respond",),
        harm_status={"respond": "harmed"},
        gate=GateFindings(statistical="SHIP", k_gate=1, code_failures=(failure,)),
    )
    assert result.action.state == "policy_code_check"
    assert result.action.verdict == "fail"
