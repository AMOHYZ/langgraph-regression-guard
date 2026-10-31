"""T063: how far each judge agrees with human labels (pure).

Rows come from a labels CSV (case_id, judge_id, judge_label, human_label, and optionally
judge_confidence and question_form) and from the overrides `review.apply_decision` saves.
The positive class is a failure, a label scoring under 0.5 in the KPI's score_map: TPR is
the share of human-failed cases the judge also failed, TNR the share of human-passed cases
it passed.
"""

from __future__ import annotations

import csv
import io
import json
import math
import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean

from lgcov.stats import wilson

BINARY: Mapping[str, float] = {"pass": 1.0, "fail": 0.0}
COLUMNS = ("case_id", "judge_id", "judge_label", "human_label")
OPTIONAL = ("judge_confidence", "question_form")
_Z = 1.959963984540054


@dataclass(frozen=True)
class Row:
    case_id: str
    judge_id: str
    judge_label: str
    human_label: str
    judge_confidence: float | None = None  # the judge's confidence in its own label
    question_form: str = ""  # Jev's confidence shifts between question forms


def read_labels(text: str) -> list[Row]:
    """A labels CSV. Every row needs the four COLUMNS; confidence, if given, is in 0..1."""
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"labels CSV: missing columns {missing}; it needs {list(COLUMNS)}")
    rows = []
    for line, raw in enumerate(reader, start=2):  # the header is line 1
        v = {c: (raw.get(c) or "").strip() for c in (*COLUMNS, *OPTIONAL)}
        empty = [c for c in COLUMNS if not v[c]]
        if empty:
            raise ValueError(f"labels CSV line {line}: empty {empty}")
        confidence = None
        if v["judge_confidence"]:
            try:
                confidence = float(v["judge_confidence"])
            except ValueError:
                confidence = math.nan
            if not 0 <= confidence <= 1:
                raise ValueError(f"labels CSV line {line}: judge_confidence must be in 0..1")
        rows.append(
            Row(
                v["case_id"],
                v["judge_id"],
                v["judge_label"],
                v["human_label"],
                confidence,
                v["question_form"],
            )
        )
    return rows


def rows_from_overrides(
    lines: Iterable[str], score_maps: Mapping[str, Mapping[str, float]]
) -> tuple[list[Row], int]:
    """One row per override that names its KPI and the judge's label, with the human's
    score mapped back to the KPI's label of that score. Others are counted, never guessed."""
    rows, skipped = [], 0
    for i, line in enumerate(text for text in lines if text.strip()):
        raw: object = json.loads(line)
        record = raw if isinstance(raw, dict) else {}
        kpi, judged, score = record.get("kpi"), record.get("judge_label"), record.get("human_label")
        if not (isinstance(kpi, str) and kpi and isinstance(judged, str) and judged):
            skipped += 1
            continue
        labels = score_maps.get(kpi, BINARY)
        human = next((label for label, s in labels.items() if s == score), None)
        if human is None or isinstance(score, bool):
            skipped += 1
            continue
        case = record.get("case_id")
        rows.append(
            Row(case if isinstance(case, str) and case else f"override-{i}", kpi, judged, human)
        )
    return rows, skipped


def _agreement(pairs: Sequence[tuple[str, str]]) -> tuple[float, float]:
    # p_o, observed agreement, and p_e, the agreement two raters with these margins expect
    n = len(pairs)
    judge, human = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    p_o = sum(1 for a, b in pairs if a == b) / n
    return p_o, sum(judge[k] * human[k] for k in judge) / (n * n)


def cohen_kappa(pairs: Sequence[tuple[str, str]]) -> float | None:
    """(judge label, human label) pairs. None when p_e is 1: both sides used one label."""
    p_o, p_e = _agreement(pairs)
    return None if p_e >= 1 else (p_o - p_e) / (1 - p_e)


def kappa_ci(
    pairs: Sequence[tuple[str, str]], resamples: int = 2000, seed: int = 0
) -> tuple[float, float] | None:
    """Seeded percentile bootstrap 95% CI for kappa, over cases."""
    rng = random.Random(seed)
    boot = sorted(
        k
        for k in (cohen_kappa(rng.choices(pairs, k=len(pairs))) for _ in range(resamples))
        if k is not None
    )
    if not boot:
        return None
    return boot[int(0.025 * len(boot))], boot[min(len(boot) - 1, int(0.975 * len(boot)))]


@dataclass(frozen=True)
class LabelsNeeded:
    half_width: float  # of kappa's 95% CI at this n
    n_for_010: int  # labels for a half-width of 0.10


def labels_needed(p_o: float, p_e: float, n: int) -> LabelsNeeded | None:
    """Kappa's 95% CI half-width ~ 1.96 sqrt(p_o (1 - p_o) / (n (1 - p_e)^2)). None where
    the approximation says nothing: agreement of 0 or 1, or p_e of 1."""
    if p_e >= 1 or not 0 < p_o < 1:
        return None
    spread = p_o * (1 - p_o) / (1 - p_e) ** 2
    return LabelsNeeded(_Z * math.sqrt(spread / n), math.ceil(_Z * _Z * spread / 0.01))


@dataclass(frozen=True)
class Bin:
    confidence: float  # one operating point, on the 0.01 grid
    n: int
    accuracy: float  # share where the judge's label matched the human's


def _log_sigmoid(x: float) -> float:
    return -math.log1p(math.exp(-x)) if x >= 0 else x - math.log1p(math.exp(x))


def fit_temperature(scored: Sequence[tuple[float, bool]]) -> float:
    """T for p = sigmoid(logit(confidence) / T) with the least log loss on whether the judge
    was right. Confidence is clamped off 0 and 1, so a pile-up at 1.00 stays finite."""
    logits = [math.log(c / (1 - c)) for c in (min(max(c, 0.005), 0.995) for c, _ in scored)]

    def loss(log_t: float) -> float:
        t = math.exp(log_t)
        return -sum(
            _log_sigmoid(z / t) if right else _log_sigmoid(-z / t)
            for z, (_, right) in zip(logits, scored, strict=True)
        )

    lo, hi = math.log(0.05), math.log(20.0)
    golden = (math.sqrt(5) - 1) / 2
    for _ in range(80):  # golden-section search on log T; the loss is unimodal in it
        a, b = hi - golden * (hi - lo), lo + golden * (hi - lo)
        if loss(a) < loss(b):
            hi = b
        else:
            lo = a
    return math.exp((lo + hi) / 2)


@dataclass(frozen=True)
class Threshold:
    cutoff: float  # call it a failure when P(fail) >= cutoff
    cv_balanced_accuracy: float  # 5-fold cross-validated


def _balanced_accuracy(scored: Sequence[tuple[float, bool]], cutoff: float) -> float:
    caught = [s >= cutoff for s, failed in scored if failed]
    cleared = [s < cutoff for s, failed in scored if not failed]
    rates = [fmean(r) for r in (caught, cleared) if r]
    return fmean(rates) if rates else 0.0


def _best_cutoff(scored: Sequence[tuple[float, bool]]) -> float:
    cutoffs = sorted({s for s, _ in scored})
    return max(cutoffs, key=lambda t: _balanced_accuracy(scored, t))  # ties: the lowest


def tune_threshold(
    scored: Sequence[tuple[float, bool]], folds: int = 5, seed: int = 0, min_n: int = 50
) -> Threshold | None:
    """(P(fail) from the judge, whether a human failed the case). The cutoff with the best
    balanced accuracy, and that choice's seeded 5-fold CV score. None below min_n labels."""
    if len(scored) < min_n:
        return None
    order = list(range(len(scored)))
    random.Random(seed).shuffle(order)
    parts = [order[k::folds] for k in range(folds)]
    held_out = []
    for k, test in enumerate(parts):
        train = [scored[i] for j, part in enumerate(parts) if j != k for i in part]
        held_out.append(_balanced_accuracy([scored[i] for i in test], _best_cutoff(train)))
    return Threshold(_best_cutoff(scored), fmean(held_out))


@dataclass(frozen=True)
class JudgeStats:
    judge_id: str
    n: int
    positives: int  # cases a human failed
    negatives: int  # cases a human passed
    tpr: float | None  # catches human-failed cases; None with no such case
    tpr_ci: tuple[float, float] | None  # Wilson 95%
    tnr: float | None  # clears human-passed cases
    tnr_ci: tuple[float, float] | None
    prevalence: float  # share of cases a human failed
    agreement: float  # share where the labels match exactly
    kappa: float | None
    kappa_ci: tuple[float, float] | None
    needed: LabelsNeeded | None
    brier: float | None = None  # None: no confidences
    reliability: tuple[Bin, ...] = ()
    temperatures: tuple[tuple[str, float], ...] = ()  # one per question_form, never pooled
    threshold: Threshold | None = None  # None: no confidences, or under 50 labels


def judge_stats(
    judge_id: str, rows: Sequence[Row], score_map: Mapping[str, float] = BINARY, seed: int = 0
) -> JudgeStats:
    """One judge's agreement with human labels. Labels must be in the KPI's score_map."""
    for r in rows:
        bad = {r.judge_label, r.human_label} - set(score_map)
        if bad:
            raise ValueError(
                f"judge '{judge_id}': labels {sorted(bad)} not in its labels {sorted(score_map)}"
            )
    failed = [(score_map[r.judge_label] < 0.5, score_map[r.human_label] < 0.5) for r in rows]
    caught = sum(1 for j, h in failed if j and h)
    cleared = sum(1 for j, h in failed if not j and not h)
    positives = sum(1 for _, h in failed if h)
    negatives = len(rows) - positives
    pairs = [(r.judge_label, r.human_label) for r in rows]
    p_o, p_e = _agreement(pairs)
    sure = [r for r in rows if r.judge_confidence is not None]
    right = [(r.judge_confidence or 0.0, r.judge_label == r.human_label) for r in sure]
    bins: dict[float, list[bool]] = defaultdict(list)
    forms: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for r, (c, ok) in zip(sure, right, strict=True):
        bins[round(c, 2)].append(ok)
        forms[r.question_form].append((c, ok))
    p_fail = [
        (c if score_map[r.judge_label] < 0.5 else 1 - c, score_map[r.human_label] < 0.5)
        for r, (c, _) in zip(sure, right, strict=True)
    ]
    return JudgeStats(
        judge_id=judge_id,
        n=len(rows),
        positives=positives,
        negatives=negatives,
        tpr=caught / positives if positives else None,
        tpr_ci=wilson(caught, positives) if positives else None,
        tnr=cleared / negatives if negatives else None,
        tnr_ci=wilson(cleared, negatives) if negatives else None,
        prevalence=positives / len(rows),
        agreement=p_o,
        kappa=cohen_kappa(pairs),
        kappa_ci=kappa_ci(pairs, seed=seed),
        needed=labels_needed(p_o, p_e, len(rows)),
        brier=fmean((c - ok) ** 2 for c, ok in right) if right else None,
        reliability=tuple(Bin(c, len(oks), fmean(oks)) for c, oks in sorted(bins.items())),
        temperatures=tuple((f, fit_temperature(s)) for f, s in sorted(forms.items())),
        threshold=tune_threshold(p_fail, seed=seed) if p_fail else None,
    )


def calibrate(
    rows: Sequence[Row], score_maps: Mapping[str, Mapping[str, float]], seed: int = 0
) -> list[JudgeStats]:
    """judge_stats for each judge in the rows, by name; a judge missing from score_maps is
    taken to be binary (pass / fail)."""
    by_judge: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_judge[r.judge_id].append(r)
    return [
        judge_stats(j, rs, score_maps.get(j, BINARY), seed) for j, rs in sorted(by_judge.items())
    ]


def _rate(p: float | None, ci: tuple[float, float] | None) -> str:
    return "—" if p is None or ci is None else f"{p:.2f} [{ci[0]:.2f}, {ci[1]:.2f}]"


def render_calibration(stats: Sequence[JudgeStats]) -> list[str]:
    """Markdown: one row per judge, then each judge's operating points, temperatures and
    threshold, in plain words."""
    lines = [
        "| judge | n | TPR: catches human-failed cases | TNR: clears human-passed cases "
        "| κ | agreement | failed by humans | labels for κ ±0.10 | Brier |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in stats:
        kappa = "—" if s.kappa is None else _rate(s.kappa, s.kappa_ci or (s.kappa, s.kappa))
        needed = (
            "—"
            if s.needed is None
            else f"{s.needed.n_for_010} (±{s.needed.half_width:.2f} at {s.n})"
        )
        brier = "—" if s.brier is None else f"{s.brier:.3f}"
        lines.append(
            f"| {s.judge_id} | {s.n} | {_rate(s.tpr, s.tpr_ci)} | {_rate(s.tnr, s.tnr_ci)} "
            f"| {kappa} | {s.agreement:.0%} | {s.prevalence:.0%} | {needed} | {brier} |"
        )
    lines.append("")
    for s in stats:
        if s.brier is None:
            lines.append(f"No confidences for {s.judge_id}: no Brier, reliability or threshold.")
            continue
        points = [b for b in s.reliability if b.confidence < 1]
        sure = [b for b in s.reliability if b.confidence >= 1]
        shown = ", ".join(f"{b.confidence:.2f} → {b.accuracy:.0%} right (n={b.n})" for b in points)
        pile = f"; at 1.00: {sure[0].accuracy:.0%} right (n={sure[0].n})" if sure else ""
        lines.append(f"Reliability, {s.judge_id}: {shown or 'nothing under 1.00'}{pile}.")
        temps = ", ".join(f"{form or '(no form)'} {t:.2f}" for form, t in s.temperatures)
        lines.append(
            f"Temperature per question form, {s.judge_id}: {temps} (over 1: claims more "
            "confidence than it earns; under 1: right more often than it claims)."
        )
        if s.threshold is None:
            lines.append(f"Threshold, {s.judge_id}: too few labels to tune ({s.n} < 50).")
        else:
            lines.append(
                f"Threshold, {s.judge_id}: call a failure at P(fail) ≥ {s.threshold.cutoff:.2f}"
                f" (5-fold CV balanced accuracy {s.threshold.cv_balanced_accuracy:.2f})."
            )
    return lines
