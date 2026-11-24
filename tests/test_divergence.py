"""T122: same-property divergence beyond MDE → needs_human; structural = investigate note."""

from __future__ import annotations

from lgcov.divergence import (
    BranchScore,
    Divergence,
    compare_branches,
)
from lgcov.verdict_actions import Evidence, decide_action


def test_same_property_divergence_beyond_mde_is_needs_human() -> None:
    """Two branches, checks_same_property: true; |Δ| > MDE → needs_human, never exit 1."""
    left = BranchScore(name="base", pass_rate=0.90, checks_same_property=True)
    right = BranchScore(name="head", pass_rate=0.70, checks_same_property=True)
    result = compare_branches(left, right, mde=0.05)
    assert result.kind == "same_property"
    assert result.beyond_mde is True
    assert result.reason  # non-empty
    action = decide_action(
        Evidence(statistical="SHIP", k_gate=1, same_property_divergence=result.reason)
    )
    assert action.state == "same_property_divergence"
    assert action.verdict == "needs_human"
    assert action.exit_code == 0  # never exit 1 from divergence alone


def test_structural_divergence_is_investigate_note_only() -> None:
    left = BranchScore(name="base", pass_rate=0.90, checks_same_property=False)
    right = BranchScore(name="head", pass_rate=0.70, checks_same_property=False)
    result = compare_branches(left, right, mde=0.05)
    assert result.kind == "structural"
    assert result.beyond_mde is False
    assert "investigate" in result.note.lower()
    # Structural note does not change the verdict row by itself
    action = decide_action(
        Evidence(statistical="SHIP", k_gate=1, structural_divergence_note=result.note)
    )
    assert action.state == "ship"
    assert action.exit_code == 0
    assert any("investigate" in d.lower() for d in action.details)


def test_same_property_within_mde_is_not_divergence() -> None:
    left = BranchScore(name="a", pass_rate=0.80, checks_same_property=True)
    right = BranchScore(name="b", pass_rate=0.82, checks_same_property=True)
    result = compare_branches(left, right, mde=0.05)
    assert result == Divergence(kind="same_property", beyond_mde=False, reason="", note="")
