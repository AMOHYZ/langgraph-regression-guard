"""The Action's PR comment (pure): T089's timing line, T104's lint line."""

from lgcov.ci_report import CiResult, Timing, format_timing, render_ci_report


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
    report = render_ci_report(CiResult("root-drift", 0, lint_summary="🧹 Lint: 1 warning"))
    assert "\n🧹 Lint: 1 warning\n" in report and "<details>" not in report
    report = render_ci_report(
        CiResult("root-drift", 0, lint_summary="🧹 Lint: 1 warning", lint_lines=found)
    )
    assert "\n🧹 Lint: 1 warning\n" in report
    assert "<summary>Lint findings</summary>" in report
    assert "- warn `c`: unreachable from START. Fix: add an edge into it." in report
