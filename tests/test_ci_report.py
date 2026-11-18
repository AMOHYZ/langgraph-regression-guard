"""The Action's PR comment (pure): T089's timing line, T104's lint line, T199's tally line."""

import json
from dataclasses import replace

import pytest

from lgcov.ci_report import (
    CASES_MARKER,
    MAX_COPIED,
    CiResult,
    Tally,
    Timing,
    cases_copy,
    format_tally,
    format_timing,
    read_cases_copy,
    render_ci_report,
)


def test_timing_line_shows_each_phase_the_counts_and_the_budget() -> None:
    t = Timing(42.0, 31.0, judges=9.0, live=3, served=1, judge_calls=12, tokens=1400, budget=90)
    assert t.overhead == 2.0  # total = agent + judges + overhead
    assert format_timing(t) == (
        "⏱ 42 s · agent 31 s (3 live / 1 served) · judges 9 s (12 calls, 1.4k tokens) · budget 90 s"
    )


def test_timing_line_names_the_node_where_the_budget_ran_out() -> None:
    t = Timing(95.0, 88.0, live=2, served=0, budget=90, budget_hit=("respond", 88.4))
    assert format_timing(t) == (
        "⏱ 95 s · agent 88 s (2 live / 0 served) · budget 90 s · budget hit at node "
        "`respond` after 88 s"
    )


def test_timing_line_without_judges_or_a_budget() -> None:
    t = Timing(0.42, 0.0123, live=0, served=5)
    assert format_timing(t) == "⏱ 0.42 s · agent 0.012 s (0 live / 5 served) · no budget"


def test_the_lint_line_and_its_findings_render_in_the_pr_comment() -> None:
    found = ("warn `c`: unreachable from START. Fix: add an edge into it.",)
    report = render_ci_report(CiResult("root-drift", lint_summary="🧹 Lint: 1 warning"))
    assert "\n🧹 Lint: 1 warning\n" in report and "<details>" not in report
    report = render_ci_report(
        CiResult("root-drift", lint_summary="🧹 Lint: 1 warning", lint_lines=found)
    )
    assert "\n🧹 Lint: 1 warning\n" in report
    assert "<summary>Lint findings</summary>" in report
    assert "- warn `c`: unreachable from START. Fix: add an edge into it." in report


TALLIES = {
    "0 runs touched": (Tally(2, 0, worse=0), "0 of 2 runs touched · 0 worse · root node: none"),
    "no root found": (Tally(2, 1), "1 of 2 runs touched · no KPI judged · root node: none"),
    "multiple roots": (
        Tally(3, 3, ("respond", "classify", "lookup_order")),
        "3 of 3 runs touched · no KPI judged · root node: `respond` +2 more",
    ),
    "worse per KPI": (
        Tally(3, 2, ("respond",), 2, (("tone", 2), ("polite", 1))),
        "2 of 3 runs touched · 2 worse (`tone` 2, `polite` 1) · root node: `respond`",
    ),
}


@pytest.mark.parametrize("case", TALLIES)
def test_the_tally_line_for_each_case(case: str) -> None:
    tally, want = TALLIES[case]
    assert format_tally(tally) == want


def test_the_tally_line_sits_under_the_heading_and_changes_nothing_else() -> None:
    table = ("| node | drift | runs |", "|---|---|---|", "| `respond` | root | 2/2 |")
    drifted = CiResult("root-drift", replay_lines=table, root_drift=("respond",))
    plain = render_ci_report(drifted).splitlines()
    tallied = render_ci_report(replace(drifted, tally=Tally(2, 2, ("respond",)))).splitlines()
    line = "2 of 2 runs touched · no KPI judged · root node: `respond`"
    assert tallied == [*plain[:3], line, "", *plain[3:]]


def test_a_comment_on_a_pr_lgcov_did_not_check_has_no_tally_line() -> None:
    report = render_ci_report(CiResult("root-drift", harness_error="boom", tally=Tally(2, 0)))
    assert "runs touched" not in report


# T186: the report's last line holds every case a model judge scored on the PR, with its labels,
# bound to the head, for `/lgcov override` to read (docs/review.md).
H = "a" * 40


def _copied(line: str) -> object:
    assert line.startswith(f"{CASES_MARKER} ") and line.endswith(" -->"), line
    return json.loads(line[len(CASES_MARKER) + 1 : -len(" -->")])


def test_the_copy_of_the_judged_cases_is_the_report_s_last_line() -> None:
    copy = cases_copy(H, {"tone": {"run-0/respond": ("fail", "pass")}})
    report = render_ci_report(CiResult("root-drift", cases_copy=copy))
    assert report.splitlines()[-1] == copy
    assert _copied(copy) == {"head": H, "kpis": {"tone": {"run-0/respond": ["fail", "pass"]}}}


def test_no_copy_without_a_head_or_a_judged_case() -> None:
    assert cases_copy(None, {"tone": {"run-0/respond": ("fail",)}}) == ""
    assert cases_copy(H, {}) == ""
    assert CASES_MARKER not in render_ci_report(CiResult("root-drift"))


def test_a_name_can_t_end_the_comment_early() -> None:
    hostile = "run-0/a--><!--b--!>"
    copy = cases_copy(H, {"x>": {hostile: ("fail",)}})
    assert copy.count("-->") == 1 and copy.count("<!--") == 1 and ">" not in copy[:-1]
    assert _copied(copy) == {"head": H, "kpis": {"x>": {hostile: ["fail"]}}}


def test_too_many_cases_leave_only_their_count() -> None:
    many = {"tone": {f"run-{i}/respond": ("pass",) for i in range(MAX_COPIED + 1)}}
    assert _copied(cases_copy(H, many)) == {"head": H, "kpis": None, "cases": MAX_COPIED + 1}


def test_the_copy_reads_back() -> None:
    kpis = {"tone": {"run-0/a-->b": ("fail", "pass")}}
    assert read_cases_copy(cases_copy(H, kpis)) == (H, {"tone": {"run-0/a-->b": ("fail", "pass")}})
    many = {"tone": {f"run-{i}/r": ("pass",) for i in range(MAX_COPIED + 1)}}
    assert read_cases_copy(cases_copy(H, many)) == (H, None)


@pytest.mark.parametrize(
    "line",
    [
        "",
        "fail-on: `root-drift`",
        f"{CASES_MARKER} not json -->",
        f'{CASES_MARKER} {{"head": 1, "kpis": {{}}}} -->',
        f'{CASES_MARKER} {{"head": "{H}", "kpis": {{"t": {{"c": "fail"}}}}}} -->',
        f'{CASES_MARKER} {{"head": "{H}", "kpis": {{"t": {{"c": [1]}}}}}} -->',
    ],
)
def test_a_line_that_isn_t_a_copy_reads_as_none(line: str) -> None:
    assert read_cases_copy(line) is None
