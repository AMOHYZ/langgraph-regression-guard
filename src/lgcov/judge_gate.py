from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import fmean
from typing import Literal

from lgcov.judges import JudgeInfraError, Verdict
from lgcov.stats import Decision, decide, paired_test

Judge = Callable[[str], float]  # one output -> score in [0, 1]


@dataclass(frozen=True)
class GateResult:
    passed: bool
    base_mean: float
    new_mean: float
    drop_ci: tuple[float, float]  # 95% CI of (base_mean - new_mean)


def bootstrap_drop_ci(
    base: Sequence[float], new: Sequence[float], n_boot: int = 2000, seed: int = 0
) -> tuple[float, float]:
    rng = random.Random(seed)
    drops = sorted(
        fmean(rng.choices(base, k=len(base))) - fmean(rng.choices(new, k=len(new)))
        for _ in range(n_boot)
    )
    return drops[int(0.025 * n_boot)], drops[int(0.975 * n_boot) - 1]


def judge_gate(
    base_outputs: Sequence[str],
    new_outputs: Sequence[str],
    judge: Judge,
    max_drop: float = 0.05,
) -> GateResult:
    base = [judge(o) for o in base_outputs]
    new = [judge(o) for o in new_outputs]
    lo, hi = bootstrap_drop_ci(base, new)
    # Fail only when we're confident the drop is bigger than the tolerance.
    return GateResult(lo <= max_drop, fmean(base), fmean(new), (lo, hi))


GateOutcome = Literal["pass", "fail", "needs_human", "infra_error"]
# T091: the old names stay, as aliases of the paired gate's decisions; infra is its own path.
OUTCOMES: dict[Decision, GateOutcome] = {
    "SHIP": "pass",
    "BLOCK": "fail",
    "INCONCLUSIVE": "needs_human",
}


def gate_outcome(
    decision: Decision, confidence: float, min_confidence: float = 0.7, blocking: bool = True
) -> GateOutcome:
    """A BLOCK goes to a human instead when the judge isn't sure or the KPI is advisory."""
    if decision == "BLOCK" and (confidence < min_confidence or not blocking):
        return "needs_human"
    return OUTCOMES[decision]


def verdict_gate(
    base: Sequence[Verdict | JudgeInfraError],
    new: Sequence[Verdict | JudgeInfraError],
    margin: float = 0.05,
    min_confidence: float = 0.7,
    alpha: float = 0.05,
) -> GateOutcome:
    """T062, T091: gate one KPI's verdicts, case i on base paired with case i on the PR.
    An infra error on either side is never a 'fail'."""
    if any(isinstance(o, JudgeInfraError) for o in (*base, *new)):
        return "infra_error"
    base_v = [v for v in base if isinstance(v, Verdict)]
    new_v = [v for v in new if isinstance(v, Verdict)]
    if not base_v or not new_v:
        return "needs_human"
    r = paired_test([[v.score] for v in base_v], [[v.score] for v in new_v], alpha)
    decision = decide(r.delta, r.ci, margin, r.p <= alpha)
    return gate_outcome(decision, fmean(v.confidence for v in new_v), min_confidence)
