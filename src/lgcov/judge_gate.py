from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean
from typing import Literal

from lgcov.judges import JudgeInfraError, Verdict
from lgcov.stats import Decision, decide, paired_test

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
