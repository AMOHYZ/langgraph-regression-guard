"""T156: one record per PR, and the summary of them. The fixture's record is made up, so its
numbers are not a measurement of anything."""

import json
from dataclasses import replace
from pathlib import Path
from typing import get_args

import pytest

from lgcov.cli import main
from lgcov.metrics import (
    MetricsSummary,
    PrMetrics,
    format_summary,
    parse_record,
    parse_records,
    record_to_json,
    summarize,
)
from lgcov.verdict_actions import ReviewVerdict

FIXTURE = Path(__file__).parent / "fixtures" / "metrics" / "one_pr.jsonl"
LINE = FIXTURE.read_text(encoding="utf-8").strip()
PLAIN = PrMetrics(
    pr="example/agent#2",
    verdict="pass",
    overridden=False,
    override_reason="",
    wall_seconds=10.0,
    api_spend_usd=None,
    cases=1,
    repeats=1,
    judge_calls=0,
    labels_added=0,
    time_to_node_minutes=None,
)


def _prs(*records: PrMetrics) -> tuple[PrMetrics, ...]:
    return tuple(replace(r, pr=f"example/agent#{i}") for i, r in enumerate(records))


def _overridden(record: PrMetrics) -> PrMetrics:
    return replace(record, overridden=True, override_reason="the judge was wrong")


def test_the_fixture_survives_the_round_trip_byte_for_byte() -> None:
    assert record_to_json(parse_record(LINE)) == LINE


@pytest.mark.parametrize("verdict", get_args(ReviewVerdict))
def test_every_verdict_and_unknown_values_survive_the_round_trip(verdict: ReviewVerdict) -> None:
    record = replace(PLAIN, verdict=verdict)
    assert parse_record(record_to_json(record)) == record


def test_a_whole_number_is_accepted_for_a_decimal_field() -> None:
    assert parse_record(LINE.replace("180.0", "180")).wall_seconds == 180.0


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema", 2, "schema"),
        ("schema", True, "schema"),
        ("pr", "  ", "pr"),
        ("pr", 1, "pr"),
        ("verdict", "ship", "verdict"),
        ("verdict", [], "verdict"),
        ("overridden", 1, "overridden"),
        ("override_reason", None, "override_reason"),
        ("override_reason", "  ", "override_reason"),
        ("wall_seconds", -1.0, "wall_seconds"),
        ("wall_seconds", True, "wall_seconds"),
        ("wall_seconds", "180", "wall_seconds"),
        ("wall_seconds", None, "wall_seconds"),
        ("wall_seconds", float("nan"), "wall_seconds"),
        ("wall_seconds", float("inf"), "wall_seconds"),
        ("wall_seconds", 10**400, "wall_seconds"),
        ("api_spend_usd", -0.01, "api_spend_usd"),
        ("time_to_node_minutes", "soon", "time_to_node_minutes"),
        ("cases", -1, "cases"),
        ("cases", 1.5, "cases"),
        ("repeats", True, "repeats"),
        ("judge_calls", None, "judge_calls"),
        ("labels_added", -1, "labels_added"),
    ],
)
def test_a_bad_value_is_rejected_by_name(field: str, value: object, message: str) -> None:
    raw = json.loads(LINE)
    raw[field] = value
    with pytest.raises(ValueError, match=message):
        parse_record(json.dumps(raw))


def test_a_reason_without_an_override_is_rejected() -> None:
    text = record_to_json(replace(PLAIN, override_reason="why"))
    with pytest.raises(ValueError, match="override_reason"):
        parse_record(text)


@pytest.mark.parametrize("text", ["[]", "not json"])
def test_a_line_that_is_not_a_record_is_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        parse_record(text)


def test_missing_and_unknown_fields_are_named() -> None:
    raw = json.loads(LINE)
    del raw["cases"]
    raw["casez"] = 6
    with pytest.raises(ValueError, match=r"missing.*cases.*unknown.*casez"):
        parse_record(json.dumps(raw))


def test_records_are_read_by_line_and_blank_lines_are_skipped() -> None:
    assert parse_records(f"\n{LINE}\n\n") == (parse_record(LINE),)
    assert parse_records("") == ()


def test_a_bad_record_names_its_line() -> None:
    with pytest.raises(ValueError, match="line 2"):
        parse_records(f"{LINE}\nnot json\n")


def test_a_repeated_pr_is_rejected_because_it_would_skew_the_rates() -> None:
    with pytest.raises(ValueError, match=r"line 2.*example/agent#1"):
        parse_records(f"{LINE}\n{LINE}\n")


def test_only_a_fail_that_was_overridden_is_an_overridden_block() -> None:
    summary = summarize(
        _prs(
            _overridden(replace(PLAIN, verdict="fail")),
            replace(PLAIN, verdict="fail"),
            _overridden(replace(PLAIN, verdict="needs_human")),
            _overridden(replace(PLAIN, verdict="advisory")),
            _overridden(PLAIN),
        )
    )
    assert (summary.overridden_blocks, summary.blocks, summary.checked) == (1, 2, 5)
    assert summary.overridden_blocks_per_100 == 20.0


def test_an_error_pr_counts_as_a_harness_error_and_nowhere_else() -> None:
    error = replace(
        _overridden(PLAIN),
        verdict="error",
        wall_seconds=1000.0,
        api_spend_usd=9.0,
        labels_added=9,
        time_to_node_minutes=99.0,
    )
    summary = summarize(_prs(error, replace(PLAIN, verdict="fail"), PLAIN, PLAIN))
    assert (summary.prs, summary.checked, summary.harness_errors) == (4, 3, 1)
    assert summary.harness_error_rate == 25.0
    assert summary.overridden_blocks_per_100 == 0.0
    assert summary.wall_p95 == 10.0
    assert (summary.labels, summary.labels_per_pr) == (0, 0.0)
    assert (summary.spend_known, summary.cost_per_pr) == (0, None)
    assert (summary.time_to_node_reported, summary.time_to_node_median) == (0, None)


def test_needs_human_is_a_rate_of_checked_prs() -> None:
    summary = summarize(_prs(replace(PLAIN, verdict="needs_human"), PLAIN))
    assert (summary.needs_human, summary.needs_human_rate) == (1, 50.0)


def test_cost_averages_only_the_prs_whose_spend_is_known() -> None:
    summary = summarize(_prs(replace(PLAIN, api_spend_usd=0.5), PLAIN, PLAIN))
    assert (summary.spend_known, summary.cost_per_pr) == (1, 0.5)


def test_a_known_spend_of_zero_is_known() -> None:
    summary = summarize(_prs(replace(PLAIN, api_spend_usd=0.0), PLAIN))
    assert (summary.spend_known, summary.cost_per_pr) == (1, 0.0)


def test_wall_time_percentiles_are_recorded_times_by_nearest_rank() -> None:
    times = [float(s) for s in range(1, 21)]
    summary = summarize(_prs(*(replace(PLAIN, wall_seconds=s) for s in reversed(times))))
    assert (summary.wall_p50, summary.wall_p95) == (10.0, 19.0)
    one = summarize((replace(PLAIN, wall_seconds=7.0),))
    assert (one.wall_p50, one.wall_p95) == (7.0, 7.0)


def test_time_to_node_is_a_median_of_the_prs_that_reported_one() -> None:
    summary = summarize(
        _prs(
            replace(PLAIN, time_to_node_minutes=10.0),
            replace(PLAIN, time_to_node_minutes=20.0),
            PLAIN,
        )
    )
    assert (summary.time_to_node_reported, summary.time_to_node_median) == (2, 15.0)


def test_no_records_gives_no_rates_rather_than_zero() -> None:
    assert summarize(()) == MetricsSummary()


FIXTURE_OUTPUT = """\
PRs: 1 (1 checked)
Harness errors: 0 of 1 PRs (0.0%)
Overridden blocks per 100 checked PRs: 100.0 (1 overridden of 1 blocks, 1 checked PRs)
needs_human: 0 of 1 checked PRs (0.0%)
Label burden: 2.0 labels per checked PR (2 labels, 1 checked PRs)
Cost: $0.42 per checked PR (known on 1 of 1 checked PRs)
Wall time: p50 180 s / p95 180 s (1 checked PRs)
Time to node: median 12 min (reported on 1 of 1 checked PRs)"""
EMPTY_OUTPUT = """\
PRs: 0 (0 checked)
Harness errors: 0 of 0 PRs (n/a)
Overridden blocks per 100 checked PRs: n/a (0 overridden of 0 blocks, 0 checked PRs)
needs_human: 0 of 0 checked PRs (n/a)
Label burden: n/a (0 labels, 0 checked PRs)
Cost: n/a (known on 0 of 0 checked PRs)
Wall time: n/a (0 checked PRs)
Time to node: n/a (reported on 0 of 0 checked PRs)"""


def test_the_summary_prints_a_count_next_to_every_rate() -> None:
    assert format_summary(summarize(parse_records(LINE))) == FIXTURE_OUTPUT


def test_an_empty_summary_prints_na() -> None:
    assert format_summary(summarize(())) == EMPTY_OUTPUT


def test_a_cost_under_a_cent_does_not_read_as_free() -> None:
    summary = summarize((replace(PLAIN, api_spend_usd=0.004),))
    assert "Cost: <$0.01 per checked PR" in format_summary(summary)


def test_the_cli_prints_the_summary(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["metrics", "summary", str(FIXTURE)]) == 0
    assert capsys.readouterr().out == FIXTURE_OUTPUT + "\n"


def test_the_cli_exits_zero_on_an_empty_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "prs.jsonl"
    empty.write_text("", encoding="utf-8")
    assert main(["metrics", "summary", str(empty)]) == 0
    assert capsys.readouterr().out == EMPTY_OUTPUT + "\n"


def test_the_cli_exits_two_on_a_missing_file(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["metrics", "summary", "no-such-file.jsonl"]) == 2
    assert "lgcov metrics summary:" in capsys.readouterr().err


def test_the_cli_exits_two_on_an_invalid_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "prs.jsonl"
    bad.write_text("not json\n", encoding="utf-8")
    assert main(["metrics", "summary", str(bad)]) == 2
    assert "lgcov metrics summary: line 1" in capsys.readouterr().err


def test_bare_metrics_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["metrics"]) == 2
    assert "lgcov metrics summary" in capsys.readouterr().err
