"""T052: node/edge coverage of a snapshot from execution traces."""

from fixtures.build_examples import build_conditional, build_linear
from fixtures.graphs import linear

from lgcov.coverage import coverage
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
