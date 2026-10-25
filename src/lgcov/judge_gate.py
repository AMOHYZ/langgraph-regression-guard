from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import fmean

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
