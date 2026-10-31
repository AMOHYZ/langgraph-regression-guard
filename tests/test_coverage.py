"""T052: node/edge coverage of a snapshot from execution traces."""

import json
from pathlib import Path

import pytest
from fixtures.build_examples import build_conditional, build_linear
from fixtures.graphs import linear

from lgcov.coverage import (
    CoverageReport,
    coverage,
    format_coverage_delta,
    load_coverage,
    write_coverage,
)
from lgcov.snapshot import Edge, snapshot_from_compiled
from lgcov.trace import record_trace


def test_two_of_four_nodes_is_fifty_percent() -> None:
    snap = snapshot_from_compiled(build_linear())
    runs = [["a", "b"], ["a", "b"]]
    rep = coverage(snap, runs)
    assert rep.node_pct == 50.0
    assert rep.unhit_nodes == ["__end__", "__start__"]
    assert rep.unhit_edges == [Edge("__start__", "a"), Edge("b", "__end__")]


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
    rep = coverage(snap, [["router", "tool"], ["router", "answer"]])
    assert rep.node_pct == 60.0  # 3 of 5 visited; __start__/__end__ never traced
    assert rep.edge_pct == 40.0  # 2 of 5: both conditional branches hit
    assert rep.unhit_edges == [
        Edge("__start__", "router"),
        Edge("answer", "__end__"),
        Edge("tool", "__end__"),
    ]


def test_t055_trace_feeds_coverage() -> None:
    snap = snapshot_from_compiled(linear())
    runs = [record_trace(linear(), {"x": 1})]  # ["a", "b"]
    rep = coverage(snap, runs)
    # Traces record node names only, so __start__/__end__ stay unhit in v0.
    assert rep.node_pct == 50.0


# T083: coverage.json artifact + delta vs a main baseline.


@pytest.mark.parametrize(
    ("current", "baseline", "want"),
    [
        (75.0, 100.0, "75.0% (-25.0% vs main)"),
        (60.0, 50.0, "60.0% (+10.0% vs main)"),
        (80.0, 80.0, "80.0% (+0.0% vs main)"),
        (75.0, None, "75.0% (no baseline)"),
    ],
)
def test_format_coverage_delta(current: float, baseline: float | None, want: str) -> None:
    assert format_coverage_delta(current, baseline) == want


def test_coverage_json_round_trip_stable_key_order(tmp_path: Path) -> None:
    rep = CoverageReport(
        node_pct=50.0,
        edge_pct=33.3,
        unhit_nodes=["__end__", "__start__"],
        unhit_edges=[Edge("__start__", "a"), Edge("b", "__end__")],
    )
    path = tmp_path / "coverage.json"
    write_coverage(path, rep)
    body = path.read_text(encoding="utf-8")
    assert list(json.loads(body)) == [
        "edge_pct",
        "node_pct",
        "unhit_edges",
        "unhit_nodes",
    ]
    assert load_coverage(path) == rep
