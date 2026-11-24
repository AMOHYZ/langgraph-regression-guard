"""T122: divergence monitor — same-property vs structural anchors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class BranchScore:
    name: str
    pass_rate: float
    checks_same_property: bool


@dataclass(frozen=True)
class Divergence:
    kind: Literal["same_property", "structural"]
    beyond_mde: bool
    reason: str  # same-property beyond MDE → feeds needs_human
    note: str  # structural → investigate note in PR comment only


def compare_branches(left: BranchScore, right: BranchScore, *, mde: float) -> Divergence:
    """Compare two branch scores.

    When both declare `checks_same_property`, a |Δ| > MDE is same-property divergence
    (needs_human). Otherwise the gap is structural (investigate note only).
    """
    delta = abs(left.pass_rate - right.pass_rate)
    if left.checks_same_property and right.checks_same_property:
        if delta > mde:
            reason = (
                f"same-property divergence: {left.name} pass_rate={left.pass_rate:.3f} vs "
                f"{right.name} pass_rate={right.pass_rate:.3f} (|Δ|={delta:.3f} > MDE={mde:.3f})"
            )
            return Divergence(kind="same_property", beyond_mde=True, reason=reason, note="")
        return Divergence(kind="same_property", beyond_mde=False, reason="", note="")
    note = (
        f"investigate: structural divergence between {left.name} and {right.name} "
        f"(|Δ|={delta:.3f}); anchors are not checks_same_property"
    )
    return Divergence(kind="structural", beyond_mde=False, reason="", note=note)
