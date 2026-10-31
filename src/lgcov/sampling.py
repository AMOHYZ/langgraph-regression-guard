"""T073: judge each case k times; pass@k, pass^k, flakiness and cost per KPI.

Uses only the Judge protocol. A sample passes when its score is at least 0.5 (a binary
KPI scores pass 1.0, fail 0.0). Infra errors never count toward k and never fail: they
are counted separately, and a KPI is an infra_error only when a side has no valid sample.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean

from lgcov.judge_gate import GateOutcome, bootstrap_drop_ci, verdict_gate
from lgcov.judges import KPI, Judge, JudgeInfraError, NodeState, Verdict

# Gateways name token counts either way; both are accepted.
_USAGE_KEYS = (("input_tokens", "output_tokens"), ("prompt_tokens", "completion_tokens"))


@dataclass(frozen=True)
class Sample:
    passed: bool | None  # None: the judge had an infra error, so this isn't one of the k
    confidence: float
    ms: float
    tokens_in: int | None = None  # None: the judge reported no usage
    tokens_out: int | None = None


def usage_tokens(raw: Mapping[str, object]) -> tuple[int, int] | None:
    """(tokens in, tokens out) from a verdict's raw["usage"], or None if absent."""
    usage = raw.get("usage")
    if not isinstance(usage, Mapping):
        return None
    for key_in, key_out in _USAGE_KEYS:
        t_in, t_out = usage.get(key_in), usage.get(key_out)
        if isinstance(t_in, int) and isinstance(t_out, int):
            return t_in, t_out
    return None


def judge_samples(
    judge: Judge,
    node_input: NodeState,
    node_output: NodeState,
    k: int,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[Sample, ...]:
    """Call the judge k times on one case, timing each call."""
    if k < 1:
        raise ValueError(f"k must be at least 1, got {k}")
    samples: list[Sample] = []
    for _ in range(k):
        start = clock()
        try:
            v = judge.judge(node_input, node_output)
        except JudgeInfraError:
            samples.append(Sample(None, 0.0, (clock() - start) * 1000))
            continue
        ms = (clock() - start) * 1000
        t_in, t_out = usage_tokens(v.raw) or (None, None)
        samples.append(Sample(v.score >= 0.5, v.confidence, ms, t_in, t_out))
    return tuple(samples)


@dataclass(frozen=True)
class CaseStats:
    case_id: str
    k: int  # valid samples; infra errors excluded
    infra: int
    passes: int
    confidence: float  # mean over valid samples; 0.0 when there are none

    @property
    def pass_at_k(self) -> bool:
        return self.passes > 0

    @property
    def pass_hat_k(self) -> bool:
        return self.k > 0 and self.passes == self.k

    @property
    def flaky(self) -> bool:
        return self.pass_at_k and not self.pass_hat_k


def case_stats(case_id: str, samples: Sequence[Sample]) -> CaseStats:
    valid = [s for s in samples if s.passed is not None]
    return CaseStats(
        case_id=case_id,
        k=len(valid),
        infra=len(samples) - len(valid),
        passes=sum(1 for s in valid if s.passed),
        confidence=fmean(s.confidence for s in valid) if valid else 0.0,
    )


@dataclass(frozen=True)
class KpiStats:
    kpi: str
    n: int  # new-side cases with at least one valid sample
    k: int  # samples asked per case
    pass_at_k: float  # rates over the n cases
    pass_hat_k: float
    infra: int  # infra samples on both sides; never counted as fails
    outcome: GateOutcome
    drop_ci: tuple[float, float] | None  # 95% CI of the base-to-new pass^k drop
    ms_total: float
    ms_mean: float  # per judgment (judge call)
    tokens_total: int | None  # None: no judgment reported usage
    tokens_mean: float | None
    flaky_cases: tuple[str, ...]  # most mixed first

    @property
    def flakiness(self) -> float:
        return self.pass_at_k - self.pass_hat_k


def _gate_verdicts(kpi: KPI, cases: Sequence[CaseStats]) -> list[Verdict]:
    # Each case's pass^k as one binary verdict, so the existing gate decides unchanged.
    return [
        Verdict(kpi, "pass" if c.pass_hat_k else "fail", float(c.pass_hat_k), c.confidence, {})
        for c in cases
        if c.k > 0
    ]


def kpi_stats(
    kpi: KPI,
    k: int,
    base: Mapping[str, Sequence[Sample]],
    new: Mapping[str, Sequence[Sample]],
    max_drop: float = 0.05,
    min_confidence: float = 0.7,
    code: bool = False,
) -> KpiStats:
    """Per-KPI rates, cost and gate outcome. `code` judges call no model: 0 tokens."""
    base_cases = [case_stats(i, s) for i, s in sorted(base.items())]
    new_cases = [case_stats(i, s) for i, s in sorted(new.items())]
    gate_kpi = KPI.binary(kpi.name, kpi.description, kpi.rubric)
    base_v, new_v = _gate_verdicts(gate_kpi, base_cases), _gate_verdicts(gate_kpi, new_cases)
    infra = sum(c.infra for c in (*base_cases, *new_cases))
    drop_ci = None
    if not base_v or not new_v:
        outcome: GateOutcome = "infra_error" if infra else "needs_human"
    else:
        outcome = verdict_gate(base_v, new_v, max_drop, min_confidence)
        drop_ci = bootstrap_drop_ci([v.score for v in base_v], [v.score for v in new_v])
    valid_new = [c for c in new_cases if c.k > 0]
    samples = [s for side in (base, new) for case in side.values() for s in case]
    reported = [
        s.tokens_in + s.tokens_out
        for s in samples
        if s.tokens_in is not None and s.tokens_out is not None
    ]
    tokens_total = 0 if code else (sum(reported) if reported else None)
    tokens_mean = 0.0 if code else (fmean(reported) if reported else None)
    flaky = sorted(
        (c for c in valid_new if c.flaky), key=lambda c: (-min(c.passes, c.k - c.passes), c.case_id)
    )
    ms_total = sum(s.ms for s in samples)
    return KpiStats(
        kpi=kpi.name,
        n=len(valid_new),
        k=k,
        pass_at_k=fmean(c.pass_at_k for c in valid_new) if valid_new else 0.0,
        pass_hat_k=fmean(c.pass_hat_k for c in valid_new) if valid_new else 0.0,
        infra=infra,
        outcome=outcome,
        drop_ci=drop_ci,
        ms_total=ms_total,
        ms_mean=ms_total / len(samples) if samples else 0.0,
        tokens_total=tokens_total,
        tokens_mean=tokens_mean,
        flaky_cases=tuple(c.case_id for c in flaky),
    )


# Same outcome icons as the PR report (report.py).
_ICONS: dict[GateOutcome, str] = {
    "pass": "🟢",
    "fail": "🔴",
    "needs_human": "🟡",
    "infra_error": "⚠️",
}


def _num(v: float) -> str:
    if 0 < v < 0.01:
        return "<0.01"
    return f"{v:,.0f}" if v >= 100 else f"{v:.3g}"


def _pct(rate: float) -> str:
    return f"{rate * 100:.0f}%"


def _tokens(s: KpiStats) -> str:
    if s.tokens_total is None or s.tokens_mean is None:
        return "—"
    if s.tokens_total == 0:
        return "0"
    return f"{_num(s.tokens_mean)} ({s.tokens_total:,} total)"


def render_kpi_table(stats: Sequence[KpiStats]) -> list[str]:
    """Markdown: one row per KPI, then each KPI's top 5 flaky cases and infra counts."""
    lines = [
        "| KPI | pass@k | pass^k | flaky | CI | ms/judgment | tokens |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in stats:
        flaky = f"⚠️ {_pct(s.flakiness)}" if s.flakiness > 0 else _pct(s.flakiness)
        ci = "—" if s.drop_ci is None else f"[{s.drop_ci[0]:+.2f}, {s.drop_ci[1]:+.2f}]"
        ms = f"{_num(s.ms_mean)} ({_num(s.ms_total)} total)"
        lines.append(
            f"| {_ICONS[s.outcome]} {s.kpi} (n={s.n}, k={s.k}) | {_pct(s.pass_at_k)} "
            f"| {_pct(s.pass_hat_k)} | {flaky} | {ci} | {ms} | {_tokens(s)} |"
        )
    lines.append("")
    for s in stats:
        if s.flaky_cases:
            cases = ", ".join(f"`{c}`" for c in s.flaky_cases[:5])
            lines.append(f"Top flaky cases, {s.kpi}: {cases}")
    infra = [f"{s.kpi} {s.infra}" for s in stats if s.infra]
    if infra:
        lines.append("Infra errors, not counted toward k: " + ", ".join(infra))
    return lines
