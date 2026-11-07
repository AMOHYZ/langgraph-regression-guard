"""T052/T083: node/edge coverage from execution traces, and its trend vs main."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fixtures.build_examples import build_conditional, build_linear
from fixtures.graphs import linear

from lgcov.coverage import (
    CoverageSnapshot,
    coverage,
    coverage_delta,
    format_coverage_delta,
    format_coverage_line,
    format_delta,
    node_floor_miss,
    snapshot_coverage,
)
from lgcov.snapshot import Edge, GraphSnapshot, snapshot_from_compiled
from lgcov.trace import record_trace


def test_completed_runs_enter_at_start_and_leave_at_end() -> None:
    snap = snapshot_from_compiled(build_linear())
    rep = coverage(snap, [["a", "b"], ["a", "b"]])  # traces omit __start__ / __end__
    assert (rep.node_pct, rep.edge_pct) == (100.0, 100.0)
    assert (rep.unhit_nodes, rep.unhit_edges) == ([], [])


def test_partial_run_leaves_the_rest_unhit() -> None:
    snap = snapshot_from_compiled(build_linear())
    rep = coverage(snap, [["a"]])
    assert rep.node_pct == 75.0
    assert rep.unhit_nodes == ["b"]
    assert rep.unhit_edges == [Edge("a", "b"), Edge("b", "__end__")]


def test_full_run_covers_everything() -> None:
    snap = snapshot_from_compiled(build_linear())
    rep = coverage(snap, [["__start__", "a", "b", "__end__"]])
    assert rep.node_pct == 100.0
    assert rep.edge_pct == 100.0
    assert rep.unhit_nodes == []
    assert rep.unhit_edges == []


def test_no_runs_means_zero_coverage() -> None:
    snap = snapshot_from_compiled(build_linear())
    rep = coverage(snap, [])
    assert rep.node_pct == 0.0
    assert rep.edge_pct == 0.0
    assert len(rep.unhit_edges) == 3


# Stretch goal: edge_pct on the conditional fixture — each branch counts.
def test_conditional_edge_pct_counts_each_branch() -> None:
    snap = snapshot_from_compiled(build_conditional())
    rep = coverage(snap, [["router", "tool"]])  # only the tool branch runs
    assert rep.node_pct == 80.0  # 4 of 5: answer never visited
    assert rep.edge_pct == 60.0  # 3 of 5: the answer branch's two edges are unhit
    assert rep.unhit_edges == [Edge("answer", "__end__"), Edge("router", "answer", True)]


def test_t055_trace_feeds_coverage() -> None:
    snap = snapshot_from_compiled(linear())
    runs = [record_trace(linear(), {"x": 1})]  # ["a", "b"]
    assert coverage(snap, runs).node_pct == 100.0


# T083: CoverageSnapshot (coverage.json), delta vs main, and the report line.
GOLDEN = Path(__file__).parent / "fixtures" / "coverage.golden.json"
DEMO_BASELINE = Path(__file__).parent / "fixtures" / "demo_baseline.json"


@pytest.mark.parametrize(
    ("pp", "want"),
    [(0.0, "±0"), (-25.0, "−25%"), (12.5, "+12.5%"), (100 / 3, "+33.3%"), (-0.04, "±0")],
)
def test_format_delta(pp: float, want: str) -> None:
    assert format_delta(pp) == want


@pytest.mark.parametrize(
    ("current", "baseline", "want"),
    [
        (75.0, 100.0, "75% (−25% vs main)"),
        (60.0, 60.0, "60% (±0)"),
        (100 / 3, 23.3, "33.3% (+10% vs main)"),
        (75.0, None, "75% (no baseline yet)"),
    ],
)
def test_format_coverage_delta(current: float, baseline: float | None, want: str) -> None:
    assert format_coverage_delta(current, baseline) == want


def _linear(runs: list[list[str]], sha: str = "") -> CoverageSnapshot:
    return snapshot_coverage(snapshot_from_compiled(build_linear()), runs, commit_sha=sha)


def test_coverage_line_without_a_baseline_says_no_baseline_yet() -> None:
    assert format_coverage_line(_linear([["a"]]), None) == (
        "Nodes 75% · Edges 33.3% (no baseline yet)"
    )


def test_coverage_line_with_a_baseline_shows_the_trend() -> None:
    line = format_coverage_line(_linear([["a"]]), _linear([["a", "b"]]))
    assert line == "Nodes 75% (−25% vs main) · Edges 33.3% (−66.7% vs main)"


# thresholds.min_node_coverage: `lgcov ci` fails a run whose node coverage is under it.
def test_node_floor_miss_names_the_floor_and_the_nodes_never_run() -> None:
    cov = _linear([["a"]])  # 75%: b never runs
    assert node_floor_miss(cov, 0) == ""
    assert node_floor_miss(cov, 75) == ""
    assert node_floor_miss(cov, 80) == (
        "Node coverage 75% is under the 80% floor (`thresholds.min_node_coverage`); never run: `b`"
    )


def test_node_floor_is_compared_as_the_report_rounds_it() -> None:
    # 2 of 3 nodes is 66.67%, shown as 66.7%: it meets a 66.7% floor, as the report reads.
    cov = replace(_linear([["a"]]), nodes_total=3, nodes_hit=2)
    assert node_floor_miss(cov, 66.7) == ""
    assert node_floor_miss(cov, 66.8).startswith("Node coverage 66.7% is under the 66.8% floor")


def test_node_hit_on_main_and_missed_on_the_pr_is_newly_uncovered() -> None:
    delta = coverage_delta(_linear([["a", "b"]]), _linear([["a"]]))
    assert delta.newly_uncovered == ("b",)
    assert delta.newly_covered == ()
    assert delta.nodes_pp == -25.0


def test_a_new_node_the_pr_never_reaches_is_not_newly_uncovered() -> None:
    base = _linear([["a", "b"]])
    snap = snapshot_from_compiled(build_linear())
    head_graph = GraphSnapshot(nodes=snap.nodes | {"c"}, edges=snap.edges)
    head = snapshot_coverage(head_graph, [["a", "b"]])
    assert head.never_hit_nodes == ("c",)
    assert coverage_delta(base, head).newly_uncovered == ()


def test_coverage_json_matches_the_golden_file_byte_for_byte() -> None:
    cov = _linear([["a"]], sha="abc123")
    golden = GOLDEN.read_text(encoding="utf-8")
    assert cov.to_json() == golden
    assert CoverageSnapshot.from_json(golden) == cov
    assert CoverageSnapshot.from_json(golden).to_json() == golden


def test_coverage_json_rejects_an_unknown_version() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="version"):
        CoverageSnapshot.from_json(json.dumps({**golden, "version": 2}))


def test_demo_fixture_coverage_is_hand_checked() -> None:
    from fixtures.demo_graph import graph

    # 5 nodes (__start__, classify, lookup_order, respond, __end__) and 5 edges; the two
    # recorded runs take both branches of classify, so every node and edge is hit.
    runs = json.loads(DEMO_BASELINE.read_text(encoding="utf-8"))["runs"]
    traces = [[step["node"] for step in run["steps"]] for run in runs]
    cov = snapshot_coverage(snapshot_from_compiled(graph), traces)
    assert (cov.nodes_total, cov.nodes_hit, cov.edges_total, cov.edges_hit) == (5, 5, 5, 5)
    assert (cov.node_pct, cov.edge_pct) == (100.0, 100.0)
