"""T073: judge each case k times; pass@k, pass^k, flakiness and cost per KPI.

Uses only the Judge protocol. A sample passes when its score is at least 0.5 (a binary
KPI scores pass 1.0, fail 0.0). Infra errors never count toward k and never fail: they
are counted separately, and a KPI is an infra_error only when a side has no valid sample.

T091: the gate pairs each case's base and PR scores (k samples averaged within the case),
then decides SHIP / BLOCK / INCONCLUSIVE per KPI, with Holm across the blocking KPIs and
Benjamini-Hochberg across the advisory ones. Flakiness is shown, never decided on.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from statistics import fmean

from lgcov.cassette import TRANSPORT_ERRORS
from lgcov.drift_gate import Outcome
from lgcov.judge_gate import GateOutcome, gate_outcome
from lgcov.judges import KPI, Judge, JudgeInfraError, NodeState
from lgcov.stats import (
    Decision,
    PairedResult,
    benjamini_hochberg,
    benjamini_hochberg_adjusted,
    decide,
    holm,
    holm_adjusted,
    paired_test,
)

# Gateways name token counts either way; both are accepted.
_USAGE_KEYS = (("input_tokens", "output_tokens"), ("prompt_tokens", "completion_tokens"))


@dataclass(frozen=True)
class Sample:
    passed: bool | None  # None: the judge had an infra error, so this isn't one of the k
    confidence: float
    ms: float
    tokens_in: int | None = None  # None: the judge reported no usage
    tokens_out: int | None = None
    score: float | None = None  # T091: the judge's score, in [0, 1]; None on an infra error


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
        except (JudgeInfraError, *TRANSPORT_ERRORS):  # T074: a judge's network trouble too
            samples.append(Sample(None, 0.0, (clock() - start) * 1000))
            continue
        ms = (clock() - start) * 1000
        t_in, t_out = usage_tokens(v.raw) or (None, None)
        samples.append(Sample(v.score >= 0.5, v.confidence, ms, t_in, t_out, v.score))
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
    paired: PairedResult | None  # T091: base vs PR, case by case; None: no case on both
    ms_total: float
    ms_mean: float  # per judgment (judge call)
    tokens_total: int | None  # None: no judgment reported usage
    tokens_mean: float | None
    flaky_cases: tuple[str, ...]  # most mixed first
    samples: int = 0  # judgments asked, both sides, infra included
    confidence: float = 0.0  # T091: mean PR-side judge confidence over the paired cases
    margin: float = 0.05  # non-inferiority margin δ
    blocking: bool = True  # False: advisory, which never fails the gate
    p_adjusted: float | None = None  # Holm (blocking) or Benjamini-Hochberg (advisory)
    decision: Decision | None = None  # None: no paired case to decide on
    roots: tuple[str, ...] = ()  # nodes that drifted first in the runs whose score changed
    band: str = "blocking"  # T063: blocking, flag (BLOCK capped at INCONCLUSIVE) or advisory

    @property
    def flakiness(self) -> float:
        return self.pass_at_k - self.pass_hat_k

    @property
    def judge_unreliable(self) -> bool:
        """T074: more than half the judgments didn't run, even if the rest passed."""
        return self.samples > 0 and self.infra * 2 > self.samples


def _scores(samples: Sequence[Sample]) -> list[float]:
    return [
        float(s.passed) if s.score is None else s.score for s in samples if s.passed is not None
    ]


def kpi_stats(
    kpi: KPI,
    k: int,
    base: Mapping[str, Sequence[Sample]],
    new: Mapping[str, Sequence[Sample]],
    margin: float = 0.05,
    min_confidence: float = 0.7,
    code: bool = False,
    alpha: float = 0.05,
    power: float = 0.8,
    case_roots: Mapping[str, str] | None = None,
) -> KpiStats:
    """Per-KPI rates, cost, and the paired gate on this KPI alone; gate_kpis decides across
    KPIs. `code` judges call no model: 0 tokens. `case_roots`: case -> the node that drifted
    first in its run."""
    base_cases = [case_stats(i, s) for i, s in sorted(base.items())]
    new_cases = [case_stats(i, s) for i, s in sorted(new.items())]
    infra = sum(c.infra for c in (*base_cases, *new_cases))
    b_scores = {c: _scores(s) for c, s in base.items()}
    n_scores = {c: _scores(s) for c, s in new.items()}
    pairs = [c for c in sorted(b_scores.keys() & n_scores.keys()) if b_scores[c] and n_scores[c]]
    paired = None
    if pairs:
        paired = paired_test(
            [b_scores[c] for c in pairs], [n_scores[c] for c in pairs], alpha, power
        )
    changed = [c for c in pairs if fmean(b_scores[c]) != fmean(n_scores[c])]
    roots = sorted({case_roots[c] for c in changed if c in case_roots}) if case_roots else []
    confidence = {c.case_id: c.confidence for c in new_cases}
    outcome: GateOutcome = "infra_error" if infra else "needs_human"  # unless pairs decide
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
    stats = KpiStats(
        kpi=kpi.name,
        n=len(valid_new),
        k=k,
        pass_at_k=fmean(c.pass_at_k for c in valid_new) if valid_new else 0.0,
        pass_hat_k=fmean(c.pass_hat_k for c in valid_new) if valid_new else 0.0,
        infra=infra,
        outcome=outcome,
        paired=paired,
        ms_total=ms_total,
        ms_mean=ms_total / len(samples) if samples else 0.0,
        tokens_total=tokens_total,
        tokens_mean=tokens_mean,
        flaky_cases=tuple(c.case_id for c in flaky),
        samples=len(samples),
        confidence=fmean(confidence[c] for c in pairs) if pairs else 0.0,
        margin=margin,
        roots=tuple(roots),
    )
    return gate_kpis([stats], alpha=alpha, min_confidence=min_confidence)[0]


def gate_kpis(
    stats: Sequence[KpiStats],
    advisory: Collection[str] = (),
    alpha: float = 0.05,
    min_confidence: float = 0.7,
    flagged: Collection[str] = (),
) -> list[KpiStats]:
    """T091: Holm across the blocking KPIs, Benjamini-Hochberg across the advisory ones,
    then each KPI's decision against its margin. An advisory KPI never fails the gate, and
    (T063) a flagged one, a judge whose band is flag, stops it at most as INCONCLUSIVE."""

    def band(kpi: str) -> str:
        return "advisory" if kpi in advisory else "flag" if kpi in flagged else "blocking"

    gated = [replace(s, blocking=s.kpi not in advisory, band=band(s.kpi)) for s in stats]
    for blocking, adjust, reject in (
        (True, holm_adjusted, holm),
        (False, benjamini_hochberg_adjusted, benjamini_hochberg),
    ):
        family = [(i, s.paired) for i, s in enumerate(gated) if s.blocking == blocking and s.paired]
        pvalues = [r.p for _, r in family]
        for (i, r), p_adj, significant in zip(
            family, adjust(pvalues), reject(pvalues, alpha), strict=True
        ):
            s = gated[i]
            decision = decide(r.delta, r.ci, s.margin, significant)
            capped = s.band == "flag"
            outcome = gate_outcome(decision, s.confidence, min_confidence, blocking, capped)
            gated[i] = replace(s, p_adjusted=p_adj, decision=decision, outcome=outcome)
    return gated


def gate_exit_code(stats: Sequence[KpiStats], inconclusive_blocks: bool = False) -> Outcome:
    """T091: AGENT_FAIL when a blocking KPI is BLOCK (or INCONCLUSIVE, with
    inconclusive_blocks); else INFRA_ERROR when one had no paired case for infra errors."""
    outcomes = {s.outcome for s in stats if s.blocking}
    if "fail" in outcomes or (inconclusive_blocks and "needs_human" in outcomes):
        return Outcome.AGENT_FAIL
    return Outcome.INFRA_ERROR if "infra_error" in outcomes else Outcome.PASS


# T091: INCONCLUSIVE (needs_human) shows as ⚠️, as an infra error does.
_ICONS: dict[GateOutcome, str] = {
    "pass": "🟢",
    "fail": "🔴",
    "needs_human": "⚠️",
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


def _verdict(s: KpiStats) -> str:
    if s.decision is None:
        return "⚠️ infra error" if s.outcome == "infra_error" else "⚠️ INCONCLUSIVE (no pairs)"
    if s.blocking and s.decision == "BLOCK" and s.outcome == "needs_human":
        why = "judge band: flag" if s.band == "flag" else "low judge confidence"
        return f"⚠️ INCONCLUSIVE ({why})"
    return f"{_ICONS[s.outcome]} {s.decision}"


def _p(p: float | None) -> str:
    return "—" if p is None else "<0.001" if p < 0.001 else f"{p:.3f}"


def render_gate_table(
    stats: Sequence[KpiStats], alpha: float = 0.05, power: float = 0.8
) -> list[str]:
    """T091, markdown: one row per KPI, plus a ⚠️ row under an INCONCLUSIVE one that has
    fewer cases than it needs (N*)."""
    lines = [
        "| KPI | n | base | PR | Δ (95% CI) | p adj. | δ | MDE | N* | verdict | root |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in stats:
        name = s.kpi if s.band == "blocking" else f"{s.kpi} ({s.band})"
        roots = ", ".join(f"`{n}`" for n in s.roots) or "—"
        r = s.paired
        if r is None:
            lines.append(
                f"| {name} | 0 | — | — | — | — | {s.margin:.2f} | — | — | {_verdict(s)} | — |"
            )
            continue
        mde = "—" if r.sd < 1e-9 else f"{r.mde:.2f}"  # no spread seen: nothing to size it by
        lines.append(
            f"| {name} | {r.n} | {_pct(r.base_rate)} | {_pct(r.new_rate)} "
            f"| {r.delta:+.2f} [{r.ci[0]:+.2f}, {r.ci[1]:+.2f}] | {_p(s.p_adjusted)} "
            f"| {s.margin:.2f} | {mde} | {r.required_n or '—'} | {_verdict(s)} | {roots} |"
        )
        if s.decision == "INCONCLUSIVE" and r.required_n is not None and r.n < r.required_n:
            need = f"need {r.required_n - r.n} more cases for {s.kpi} (N*={r.required_n})"
            lines.append(f"| ⚠️ underpowered: {need} |" + " |" * 10)
    lines += [
        "",
        "Cases paired base vs PR. p adj.: Holm across blocking KPIs, Benjamini-Hochberg across "
        "advisory ones. δ: non-inferiority margin. MDE: smallest drop this n detects. N*: "
        f"cases needed to detect Δ (α {alpha:g}, power {power:g}).",
    ]
    return lines


def render_kpi_table(stats: Sequence[KpiStats]) -> list[str]:
    """Markdown: one row per KPI, then each KPI's top 5 flaky cases and infra counts. The
    verdict is in the gate table (T091): flakiness is shown here and never changes it."""
    lines = [
        "| KPI | pass@k | pass^k | flaky | ms/judgment | tokens |",
        "|---|---|---|---|---|---|",
    ]
    for s in stats:
        flaky = f"⚠️ {_pct(s.flakiness)}" if s.flakiness > 0 else _pct(s.flakiness)
        ms = f"{_num(s.ms_mean)} ({_num(s.ms_total)} total)"
        lines.append(
            f"| {s.kpi} (n={s.n}, k={s.k}) | {_pct(s.pass_at_k)} "
            f"| {_pct(s.pass_hat_k)} | {flaky} | {ms} | {_tokens(s)} |"
        )
        if s.judge_unreliable:
            why = f"{s.infra} of {s.samples} judgments didn't run"
            lines.append(f"| ⚠️ judge unreliable: {s.kpi} ({why}) | | | | | |")
    lines.append("")
    for s in stats:
        if s.flaky_cases:
            cases = ", ".join(f"`{c}`" for c in s.flaky_cases[:5])
            lines.append(f"Top flaky cases, {s.kpi}: {cases}")
    infra = [f"{s.kpi} {s.infra}" for s in stats if s.infra]
    if infra:
        lines.append("Infra errors, not counted toward k: " + ", ".join(infra))
    return lines
