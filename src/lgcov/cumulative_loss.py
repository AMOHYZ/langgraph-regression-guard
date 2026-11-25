"""T234: cumulative loss vs the kept reference round (option 2). Pure.

No summing across intermediate rounds: each candidate is tested directly against the
kept reference with the existing paired test. The budget is the KPI's non-inferiority
margin ``m``. Breach (loss beyond ``m``) is BLOCK / exit 1; INCONCLUSIVE is needs_human
(exit 0, unless the caller raises inconclusive to exit 1). Missing reference path or
hash mismatch is exit 2 via ``reference_copy_problems``, not this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from lgcov.stats import Decision, decide, paired_test

LossState = Literal["ok", "blocked", "needs_human"]


@dataclass(frozen=True)
class CumulativeLoss:
    """One paired-test outcome of a candidate against the kept reference round."""

    decision: Decision
    state: LossState
    exit_code: int  # 0 or 1; path/hash problems are exit 2 elsewhere


def loss_vs_reference(
    reference: Sequence[Sequence[float]],
    candidate: Sequence[Sequence[float]],
    margin: float,
    *,
    alpha: float = 0.05,
    power: float = 0.8,
) -> CumulativeLoss:
    """Paired test of candidate vs reference scores. Budget = ``margin`` (non-inferiority m)."""
    result = paired_test(reference, candidate, alpha=alpha, power=power)
    decision = decide(
        result.delta,
        result.ci,
        margin,
        result.p <= alpha,
        discordant=result.discordant,
        n=result.n,
        alpha=alpha,
    )
    if decision == "BLOCK":
        return CumulativeLoss(decision, "blocked", 1)
    if decision == "INCONCLUSIVE":
        return CumulativeLoss(decision, "needs_human", 0)
    return CumulativeLoss(decision, "ok", 0)
