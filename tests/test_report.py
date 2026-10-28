"""T053: markdown PR report from graph diff + coverage + judge verdicts."""

from pathlib import Path

from fixtures.build_examples import build_linear

from lgcov.code_judges import ToolNotCalled
from lgcov.coverage import CoverageReport, coverage
from lgcov.graph_diff import GraphDiff, diff_graphs
from lgcov.judges import KPI
from lgcov.report import KpiResult, render_pr_report
from lgcov.snapshot import Edge, GraphSnapshot, snapshot_from_compiled

GOLDEN = Path(__file__).parent / "fixtures" / "pr_report.golden.md"


def _fixture_diff_and_coverage() -> tuple[GraphDiff, CoverageReport]:
    old = snapshot_from_compiled(build_linear())  # 4 nodes, 3 edges
    new = GraphSnapshot(nodes=old.nodes | {"c"}, edges=old.edges - {Edge("b", "__end__")})
    return diff_graphs(old, new), coverage(old, [["a", "b"]])


def test_report_matches_golden() -> None:
    diff, cov = _fixture_diff_and_coverage()
    verdicts = [
        KpiResult("tone", "fail", 0.81, "score drop 0.18 > 0.05"),
        KpiResult("faithfulness", "pass", 0.92),
        KpiResult("completeness", "needs_human", 0.55, "low judge confidence"),
    ]
    assert render_pr_report(diff, cov, verdicts) == GOLDEN.read_text(encoding="utf-8")


def test_all_green_is_short() -> None:
    snap = snapshot_from_compiled(build_linear())
    diff = diff_graphs(snap, snap)
    cov = coverage(snap, [["__start__", "a", "b", "__end__"]])
    out = render_pr_report(diff, cov, [KpiResult("faithfulness", "pass", 0.9)])
    assert out.startswith("## 🟢 ")
    assert "No graph changes" in out
    assert "<details>" not in out
    assert len(out.splitlines()) < 25


def test_infra_error_is_not_a_regression() -> None:
    diff, cov = _fixture_diff_and_coverage()
    out = render_pr_report(diff, cov, [KpiResult("tone", "infra_error", None, "timeout")])
    assert out.startswith("## 🔴 ")
    assert "| tone | ⚠️ infra_error | — | harness error, not a regression |" in out


def test_needs_human_without_fail_is_yellow() -> None:
    diff, cov = _fixture_diff_and_coverage()
    verdicts = [KpiResult("a", "pass", 0.9), KpiResult("b", "needs_human", 0.4)]
    assert render_pr_report(diff, cov, verdicts).startswith("## 🟡 ")


def test_pipe_in_note_is_escaped() -> None:
    diff, cov = _fixture_diff_and_coverage()
    out = render_pr_report(diff, cov, [KpiResult("k", "pass", 1.0, "a|b")])
    assert "| a\\|b |" in out


def test_code_judge_verdict_renders_as_row() -> None:
    diff, cov = _fixture_diff_and_coverage()
    kpi = KPI.binary("no_web_search", "d", "r")
    v = ToolNotCalled(kpi, "web_search").judge(
        {}, {"trace": {"nodes": ["a"], "tools": ["web_search"]}}
    )
    row = KpiResult(kpi.name, "fail", v.confidence, str(v.raw["reason"]))
    out = render_pr_report(diff, cov, [row])
    assert "| no_web_search | 🔴 fail | 1.00 | tool web_search called at step 1 |" in out


def test_report_mentions_normalised_field_count() -> None:
    diff, cov = _fixture_diff_and_coverage()
    out = render_pr_report(diff, cov, [], normalised_fields=4)
    assert "Normalised 4 volatile field(s) before L1 compare." in out
    assert "Normalised" not in render_pr_report(diff, cov, [])
