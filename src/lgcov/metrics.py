"""T156: one record per PR, one JSON object per line, and the summary of them (pure).

Nothing writes these yet: records are written by hand. An overridden block is a `fail` a reviewer
overrode, so it is their disagreement and not verified truth. A checked PR is any but an `error`:
a harness error means the PR wasn't checked, so only the harness error rate counts those.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from statistics import median
from typing import get_args

from lgcov.verdict_actions import ReviewVerdict

SCHEMA = 1
_VERDICTS: tuple[ReviewVerdict, ...] = get_args(ReviewVerdict)


@dataclass(frozen=True)
class PrMetrics:
    pr: str  # owner/repo#12
    verdict: ReviewVerdict
    overridden: bool
    override_reason: str  # required if overridden, empty if not
    wall_seconds: float
    api_spend_usd: float | None  # None: not known, which isn't $0
    cases: int
    repeats: int  # k, the samples per case
    judge_calls: int
    labels_added: int
    time_to_node_minutes: float | None  # self-reported; None: nobody reported it


@dataclass(frozen=True)
class MetricsSummary:
    """Counts and rates; a rate is None when there is nothing to divide by."""

    prs: int = 0
    checked: int = 0
    harness_errors: int = 0
    harness_error_rate: float | None = None  # percent of all PRs
    blocks: int = 0
    overridden_blocks: int = 0
    overridden_blocks_per_100: float | None = None
    needs_human: int = 0
    needs_human_rate: float | None = None  # percent of checked PRs
    labels: int = 0
    labels_per_pr: float | None = None
    spend_known: int = 0
    cost_per_pr: float | None = None
    wall_p50: float | None = None
    wall_p95: float | None = None
    time_to_node_reported: int = 0
    time_to_node_median: float | None = None


_FIELDS = frozenset(f.name for f in fields(PrMetrics))


def record_to_json(record: PrMetrics) -> str:
    return json.dumps({**asdict(record), "schema": SCHEMA}, sort_keys=True)


def _count(raw: Mapping[str, object], key: str) -> int:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a whole number, 0 or more")
    return value


def _amount(raw: Mapping[str, object], key: str) -> float:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be a number, 0 or more")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(f"{key} must be a number, 0 or more") from None
    if not math.isfinite(number) or number < 0:  # json.loads accepts NaN and Infinity
        raise ValueError(f"{key} must be a number, 0 or more")
    return number


def _known_amount(raw: Mapping[str, object], key: str) -> float | None:
    return None if raw[key] is None else _amount(raw, key)


def _check_fields(raw: Mapping[str, object]) -> None:
    schema = raw.get("schema")
    if type(schema) is not int or schema != SCHEMA:
        raise ValueError(f"not a schema {SCHEMA} record")
    missing, unknown = _FIELDS - raw.keys(), raw.keys() - _FIELDS - {"schema"}
    if missing or unknown:
        raise ValueError(f"missing fields: {sorted(missing)}; unknown fields: {sorted(unknown)}")


def _verdict(raw: Mapping[str, object]) -> ReviewVerdict:
    for verdict in _VERDICTS:
        if raw["verdict"] == verdict:
            return verdict
    raise ValueError(f"verdict must be one of {', '.join(_VERDICTS)}")


def _override(raw: Mapping[str, object]) -> tuple[bool, str]:
    overridden, reason = raw["overridden"], raw["override_reason"]
    if not isinstance(overridden, bool):
        raise ValueError("overridden must be true or false")
    if not isinstance(reason, str):
        raise ValueError("override_reason must be text")
    if overridden != bool(reason.strip()):
        raise ValueError("override_reason is required on an override and only there")
    return overridden, reason


def parse_record(line: str) -> PrMetrics:
    """The record on a line. ValueError if it isn't one."""
    raw: object = json.loads(line)
    if not isinstance(raw, dict):
        raise ValueError(f"not a schema {SCHEMA} record")
    _check_fields(raw)
    pr = raw["pr"]
    if not isinstance(pr, str) or not pr.strip():
        raise ValueError("pr must name the PR")
    overridden, reason = _override(raw)
    return PrMetrics(
        pr=pr,
        verdict=_verdict(raw),
        overridden=overridden,
        override_reason=reason,
        wall_seconds=_amount(raw, "wall_seconds"),
        api_spend_usd=_known_amount(raw, "api_spend_usd"),
        cases=_count(raw, "cases"),
        repeats=_count(raw, "repeats"),
        judge_calls=_count(raw, "judge_calls"),
        labels_added=_count(raw, "labels_added"),
        time_to_node_minutes=_known_amount(raw, "time_to_node_minutes"),
    )


def parse_records(text: str) -> tuple[PrMetrics, ...]:
    """Every record in a file. A repeated PR is an error: a re-run would skew the rates."""
    records: dict[str, PrMetrics] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = parse_record(line)
        except ValueError as exc:
            raise ValueError(f"line {number}: {exc}") from None
        if record.pr in records:
            raise ValueError(f"line {number}: a second record for {record.pr}")
        records[record.pr] = record
    return tuple(records.values())


def _percent(part: int, whole: int) -> float | None:
    return part / whole * 100 if whole else None


def _nearest_rank(values: Sequence[float], percent: int) -> float | None:
    """A value that was recorded, also for one record; None for none."""
    if not values:
        return None
    return sorted(values)[math.ceil(percent * len(values) / 100) - 1]


def summarize(records: Sequence[PrMetrics]) -> MetricsSummary:
    checked = [r for r in records if r.verdict != "error"]
    blocks = [r for r in checked if r.verdict == "fail"]
    overridden = sum(r.overridden for r in blocks)
    needs_human = sum(r.verdict == "needs_human" for r in checked)
    labels = sum(r.labels_added for r in checked)
    spend = [r.api_spend_usd for r in checked if r.api_spend_usd is not None]
    walls = [r.wall_seconds for r in checked]
    minutes = [r.time_to_node_minutes for r in checked if r.time_to_node_minutes is not None]
    errors = len(records) - len(checked)
    return MetricsSummary(
        prs=len(records),
        checked=len(checked),
        harness_errors=errors,
        harness_error_rate=_percent(errors, len(records)),
        blocks=len(blocks),
        overridden_blocks=overridden,
        overridden_blocks_per_100=_percent(overridden, len(checked)),
        needs_human=needs_human,
        needs_human_rate=_percent(needs_human, len(checked)),
        labels=labels,
        labels_per_pr=labels / len(checked) if checked else None,
        spend_known=len(spend),
        cost_per_pr=sum(spend) / len(spend) if spend else None,
        wall_p50=_nearest_rank(walls, 50),
        wall_p95=_nearest_rank(walls, 95),
        time_to_node_reported=len(minutes),
        time_to_node_median=median(minutes) if minutes else None,
    )


def _rate(value: float | None, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:.1f}{suffix}"


def _cost(value: float | None) -> str:
    if value is None:
        return "n/a"
    amount = f"${value:.2f}" if value == 0 or value >= 0.01 else "<$0.01"
    return f"{amount} per checked PR"


def format_summary(s: MetricsSummary) -> str:
    """The summary's lines, with the counts next to every rate: one or two PRs make a rate look
    more certain than it is."""
    checked = f"{s.checked} checked PRs"
    burden = "n/a" if s.labels_per_pr is None else f"{s.labels_per_pr:.1f} labels per checked PR"
    wall = "n/a" if s.wall_p50 is None else f"p50 {s.wall_p50:g} s / p95 {s.wall_p95:g} s"
    node = "n/a" if s.time_to_node_median is None else f"median {s.time_to_node_median:g} min"
    overridden = f"{s.overridden_blocks} overridden of {s.blocks} blocks"
    return "\n".join(
        (
            f"PRs: {s.prs} ({s.checked} checked)",
            f"Harness errors: {s.harness_errors} of {s.prs} PRs "
            f"({_rate(s.harness_error_rate, '%')})",
            f"Overridden blocks per 100 checked PRs: {_rate(s.overridden_blocks_per_100)} "
            f"({overridden}, {checked})",
            f"needs_human: {s.needs_human} of {checked} ({_rate(s.needs_human_rate, '%')})",
            f"Label burden: {burden} ({s.labels} labels, {checked})",
            f"Cost: {_cost(s.cost_per_pr)} (known on {s.spend_known} of {checked})",
            f"Wall time: {wall} ({checked})",
            f"Time to node: {node} (reported on {s.time_to_node_reported} of {checked})",
        )
    )
