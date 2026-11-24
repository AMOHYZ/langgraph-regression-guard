"""T123: immutable negative controls in pools — rationale required; pass → needs_human."""

from __future__ import annotations

from lgcov.gate_round import JudgeLock
from lgcov.pools import (
    CaseRecord,
    LineageEntry,
    check_pools,
    pass_rate_excluding_controls,
)
from lgcov.verdict_actions import Evidence, decide_action

JUDGE = JudgeLock("jev-1", "0.1.0", "c" * 64)


def _case(
    case_id: str,
    text: str,
    pool: str,
    *,
    control: bool = False,
    rationale: str = "",
) -> CaseRecord:
    return CaseRecord(case_id, text, pool, control=control, immutability_rationale=rationale)


def test_pools_check_rejects_control_with_no_rationale() -> None:
    cases = (_case("ctrl-1", "Always refuse this", "gate", control=True, rationale=""),)
    problems = check_pools(cases, {}, JUDGE)
    assert any("ctrl-1" in p and "immutability_rationale" in p for p in problems)


def test_pools_check_rejects_control_with_human_labelled_pass() -> None:
    """Hard-but-solvable: a control must not carry a recorded human pass label."""
    cases = (
        _case(
            "ctrl-2",
            "Always refuse this",
            "gate",
            control=True,
            rationale="immutable negative: must keep failing",
        ),
    )
    problems = check_pools(cases, {}, JUDGE, human_pass_case_ids=frozenset({"ctrl-2"}))
    assert any("ctrl-2" in p and "human" in p.lower() and "pass" in p.lower() for p in problems)


def test_control_with_rationale_and_no_human_pass_is_ok() -> None:
    cases = (
        _case(
            "ctrl-ok",
            "Refuse forever",
            "gate",
            control=True,
            rationale="synthetic harm that must stay failing",
        ),
        _case("gate-1", "What is the refund policy?", "gate"),
    )
    lineage = {
        "ctrl-ok": LineageEntry("gate"),
        "gate-1": LineageEntry("gate"),
    }
    assert check_pools(cases, lineage, JUDGE) == []


def test_pass_on_control_is_needs_human_control_passed_not_block() -> None:
    action = decide_action(
        Evidence(
            statistical="SHIP",
            k_gate=1,
            control_passed="control_passed: negative control `ctrl-1` passed",
            control_audit=True,
        )
    )
    assert action.state == "control_passed"
    assert action.verdict == "needs_human"
    assert action.exit_code == 0  # v0: needs_human, not BLOCK
    assert "control_passed" in action.next_step
    assert any("Audit: control_passed" in d for d in action.details)


def test_legitimate_fix_of_hard_non_control_case_is_not_blocked() -> None:
    """A hard (but non-control) case that now passes does not trigger control_passed."""
    action = decide_action(Evidence(statistical="SHIP", k_gate=1))
    assert action.state == "ship"
    assert action.verdict == "pass"
    assert action.exit_code == 0


def test_controls_excluded_from_pass_rate_denominator() -> None:
    cases = (
        _case("a", "ok", "gate"),
        _case("b", "ok2", "gate"),
        _case(
            "ctrl",
            "bad",
            "gate",
            control=True,
            rationale="must fail",
        ),
    )
    # 2 non-controls pass, control "passes" too — rate is 2/2 not 3/3
    passed_ids = frozenset({"a", "b", "ctrl"})
    rate = pass_rate_excluding_controls(cases, passed_ids)
    assert rate == (2, 2)
