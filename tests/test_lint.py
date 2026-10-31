"""T104: graph lint (pure): what is wrong with a graph's shape, before any replay."""

from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from lgcov.lint import Finding, Router, lint, routers_of, summary
from lgcov.snapshot import Edge, GraphSnapshot, snapshot_from_compiled


class S(TypedDict, total=False):
    x: int


def _noop(state: S) -> S:
    return {}


def _to_b(state: S) -> Literal["b"]:
    return "b"


def _anywhere(state: S) -> str:  # no mapping and no Literal: it could pick any node
    return "b"


def test_a_router_mapping_to_an_unknown_node_is_one_warning_naming_the_router() -> None:
    # LangGraph's compile() refuses such a graph, so the snapshot is built by hand.
    snap = GraphSnapshot(
        nodes=frozenset({START, "a", "b", END}),
        edges=frozenset({Edge(START, "a"), Edge("a", "b", True), Edge("b", END)}),
    )
    found = lint(snap, [Router("route", "a", ("b", "ghost", "phantom"))])
    assert [(f.severity, f.node) for f in found] == [("warn", "route")]
    assert found[0].line() == (
        "warn `route`: router on `a` maps to nodes that don't exist: `ghost`, `phantom`. "
        "Fix: add them, or map to nodes that exist."
    )


def test_an_unreachable_node_is_a_warning() -> None:
    g = StateGraph(S)
    for n in ("a", "b", "c"):
        g.add_node(n, _noop)
    g.add_edge(START, "a")
    g.add_conditional_edges("a", _to_b)
    g.add_edge("b", END)
    g.add_edge("c", END)  # nothing leads to c
    graph = g.compile()
    assert routers_of(graph) == (Router("_to_b", "a", ("b",)),)
    found = lint(snapshot_from_compiled(graph), routers_of(graph), hit={"a", "b"})
    assert [(f.severity, f.node) for f in found] == [("warn", "c")]  # not "never hit" too
    assert found[0].message == "unreachable from START"


def test_a_router_with_no_mapping_may_reach_any_node() -> None:
    # get_graph() draws it as one edge to END; b and c are still reachable through it.
    g = StateGraph(S)
    for n in ("a", "b", "c"):
        g.add_node(n, _noop)
    g.add_edge(START, "a")
    g.add_conditional_edges("a", _anywhere)
    g.add_edge("b", END)
    g.add_edge("c", END)
    graph = g.compile()
    assert routers_of(graph) == (Router("_anywhere", "a", None),)
    assert lint(snapshot_from_compiled(graph), routers_of(graph)) == []


def test_a_dangling_edge_is_a_warning() -> None:
    snap = GraphSnapshot(
        nodes=frozenset({START, "a", END}),
        edges=frozenset({Edge(START, "a"), Edge("a", "ghost"), Edge("a", END)}),
    )
    found = lint(snap)
    assert [(f.severity, f.node) for f in found] == [("warn", "a")]
    assert found[0].message == "edge `a → ghost` ends at a node that doesn't exist"


def test_a_node_no_run_reached_warns_and_a_node_no_kpi_names_is_info() -> None:
    g = StateGraph(S)
    g.add_node("a", _noop)
    g.add_node("b", _noop)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    graph = g.compile()
    found = lint(snapshot_from_compiled(graph), routers_of(graph), hit={"a"}, kpi_nodes={"b"})
    assert [(f.severity, f.node, f.message) for f in found] == [
        ("warn", "b", "no recorded run reached it"),
        ("info", "a", "no KPI in the taxonomy names it"),
    ]


def test_the_summary_line_counts_warnings_and_info() -> None:
    warn, info = Finding("warn", "a", "m", "h"), Finding("info", "b", "m", "h")
    assert summary([]) == "🧹 Lint: 0 warnings"
    assert summary([warn]) == "🧹 Lint: 1 warning"
    assert summary([warn, warn, info]) == "🧹 Lint: 2 warnings · 1 info"
