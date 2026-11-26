"""T262: each drift root gets a harm status from lgcov's own KPIs/code checks only.

`classify` stays a drift label. Harm status never reads a synthetic truth or `regressed`.
fail-on: harmed-root fails only on harmed roots; unmeasured is needs_human (never a pass);
no-harm-seen alone is exit 0. root-drift stays the default.

A check that cannot observe a root's output or anything downstream of it does not measure
that root (presence on the root itself, or a check scoped only to upstream nodes).
"""

from pathlib import Path

import pytest

from lgcov.ci_report import CiResult, render_ci_report
from lgcov.drift_gate import (
    FAIL_ON_VALUES,
    HarmStatus,
    check_observes_root,
    parse_fail_on,
    root_harm_statuses,
    root_unmeasured_reasons,
)
from lgcov.taxonomy import load_taxonomy
from lgcov.verdict_actions import CodeFailure, GateFindings

ROOT = Path(__file__).resolve().parent
TAXONOMY = ROOT / "fixtures" / "harm_status" / "taxonomy.yaml"


def _specs() -> dict[str, str]:
    """KPI name -> node this fixture's code check covers."""
    return {s.name: s.node for s in load_taxonomy(TAXONOMY).kpi_specs}


def _checks() -> dict[str, dict[str, object]]:
    """KPI name -> taxonomy check mapping."""
    out: dict[str, dict[str, object]] = {}
    for s in load_taxonomy(TAXONOMY).kpi_specs:
        assert s.check is not None
        out[s.name] = dict(s.check)
    return out


def test_fixture_taxonomy_covers_respond_only() -> None:
    assert _specs() == {"respond_runs": "respond"}


def test_presence_on_root_does_not_measure() -> None:
    """Same-node node_visited that ran (pass or fail) leaves the root unmeasured."""
    covered = _specs()
    checks = _checks()
    for failed in (frozenset({"respond_runs"}), frozenset()):
        statuses = root_harm_statuses(
            ("respond", "lookup_order"),
            covered,
            failed_kpis=failed,
            judged_kpis=frozenset({"respond_runs"}),
            kpi_checks=checks,
        )
        assert statuses == {
            "respond": "unmeasured",
            "lookup_order": "unmeasured",
        }
    reasons = root_unmeasured_reasons(
        ("respond", "lookup_order"),
        covered,
        judged_kpis=frozenset({"respond_runs"}),
        kpi_checks=checks,
    )
    assert reasons["respond"] == "only a presence check covers this node"
    assert reasons["lookup_order"] == "no covering check"


def test_observing_check_still_measures() -> None:
    """A non-presence code check on the root still yields harmed / no harm seen."""
    covered = {"answer_ok": "respond"}
    checks = {"answer_ok": {"type": "tool_called", "tool": "lookup_order"}}
    harmed = root_harm_statuses(
        ("respond", "lookup_order"),
        covered,
        failed_kpis=frozenset({"answer_ok"}),
        judged_kpis=frozenset({"answer_ok"}),
        kpi_checks=checks,
    )
    assert harmed == {"respond": "harmed", "lookup_order": "unmeasured"}
    clear = root_harm_statuses(
        ("respond", "classify"),
        covered,
        failed_kpis=frozenset(),
        judged_kpis=frozenset({"answer_ok"}),
        kpi_checks=checks,
    )
    assert clear == {"respond": "no harm seen", "classify": "unmeasured"}


def test_upstream_only_check_does_not_measure() -> None:
    """A presence check on a node that only runs before the root does not measure it."""
    covered = {"early": "respond"}
    checks = {"early": {"type": "node_visited", "node": "classify"}}
    path = ("classify", "lookup_order", "respond")
    assert check_observes_root("respond", checks["early"], path=path) is False
    statuses = root_harm_statuses(
        ("respond",),
        covered,
        failed_kpis=frozenset(),
        judged_kpis=frozenset({"early"}),
        kpi_checks=checks,
        path=path,
    )
    assert statuses == {"respond": "unmeasured"}
    reasons = root_unmeasured_reasons(
        ("respond",),
        covered,
        judged_kpis=frozenset({"early"}),
        kpi_checks=checks,
        path=path,
    )
    assert reasons["respond"] == "only an upstream check covers this node"


def test_downstream_presence_check_does_measure() -> None:
    """node_visited on a later node observes something downstream of the root."""
    covered = {"later": "classify"}
    checks = {"later": {"type": "node_visited", "node": "respond"}}
    path = ("classify", "lookup_order", "respond")
    assert check_observes_root("classify", checks["later"], path=path) is True
    statuses = root_harm_statuses(
        ("classify",),
        covered,
        failed_kpis=frozenset(),
        judged_kpis=frozenset({"later"}),
        kpi_checks=checks,
        path=path,
    )
    assert statuses == {"classify": "no harm seen"}


def test_harm_status_never_takes_a_truth_label() -> None:
    """The API has no place for regressed/benign; only KPI names and nodes."""
    import inspect

    params = inspect.signature(root_harm_statuses).parameters
    assert "regressed" not in params and "truth" not in params and "label" not in params


def test_unmeasured_reason_appears_in_pr_comment() -> None:
    result = CiResult(
        "harmed-root",
        root_drift=("respond",),
        harm_status={"respond": "unmeasured"},
        harm_reasons={"respond": "only a presence check covers this node"},
        gate=GateFindings(statistical="SHIP", k_gate=1),
        tier="pr",
    )
    body = render_ci_report(result)
    assert "only a presence check covers this node" in body
    assert "`respond` unmeasured" in body


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
