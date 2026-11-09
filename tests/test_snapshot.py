"""Tests for T050 (snapshot adapter), T051 (JSON round-trip) and T207 (node tools)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fixtures.build_examples import build_conditional, build_linear
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from lgcov.snapshot import Edge, GraphSnapshot, node_tools, snapshot_from_compiled

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---- T050: snapshot adapter -------------------------------------------------


def test_linear_fixture_shape() -> None:
    snap = snapshot_from_compiled(build_linear())
    assert snap.nodes == frozenset({"__start__", "a", "b", "__end__"})
    assert len(snap.edges) == 3
    assert not any(e.conditional for e in snap.edges)


def test_conditional_edges_are_flagged() -> None:
    snap = snapshot_from_compiled(build_conditional())
    conditional = {(e.source, e.target) for e in snap.edges if e.conditional}
    assert conditional == {("router", "tool"), ("router", "answer")}
    assert Edge("__start__", "router", conditional=False) in snap.edges


def test_snapshot_matches_raw_t012_dump() -> None:
    for name, builder in (("linear", build_linear), ("conditional", build_conditional)):
        raw = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        snap = snapshot_from_compiled(builder())
        assert sorted(snap.nodes) == raw["nodes"]
        assert [e.to_dict() for e in snap.sorted_edges()] == raw["edges"]


# ---- T051: JSON round-trip --------------------------------------------------


# Builders return Any-typed compiled graphs (see fixtures.build_examples).
@pytest.mark.parametrize("builder", [build_linear, build_conditional])
def test_round_trip(builder: Callable[[], Any]) -> None:
    snap = snapshot_from_compiled(builder())
    assert GraphSnapshot.from_json(snap.to_json()) == snap


def test_to_json_is_stable() -> None:
    a = snapshot_from_compiled(build_conditional()).to_json()
    b = snapshot_from_compiled(build_conditional()).to_json()
    assert a == b


def test_linear_golden_file() -> None:
    golden = (FIXTURES / "linear.golden.json").read_text(encoding="utf-8")
    assert snapshot_from_compiled(build_linear()).to_json() == golden


def test_from_json_rejects_unknown_version() -> None:
    bad = json.dumps({"version": 99, "nodes": [], "edges": []})
    with pytest.raises(ValueError, match="version"):
        GraphSnapshot.from_json(bad)


def test_from_json_rejects_edge_to_unknown_node() -> None:
    bad = json.dumps(
        {
            "version": 1,
            "nodes": ["a"],
            "edges": [{"source": "a", "target": "zz", "conditional": False}],
        }
    )
    with pytest.raises(ValueError, match="unknown node"):
        GraphSnapshot.from_json(bad)


# ---- T207: tools exposed by each node -----------------------------------------


@dataclass(frozen=True)
class _FakeToolNode:
    tools_by_name: dict[str, object]


@dataclass(frozen=True)
class _FakeNode:
    metadata: dict[str, object] | None = None
    data: object = None


@dataclass(frozen=True)
class _FakeDrawable:
    nodes: dict[str, _FakeNode] = field(default_factory=dict)


@dataclass(frozen=True)
class _FakeCompiled:
    drawable: _FakeDrawable

    def get_graph(self) -> _FakeDrawable:
        return self.drawable


def test_node_tools_reads_a_real_toolnode() -> None:
    @tool
    def refund(order_id: str) -> str:
        """Refund an order."""
        return "ok"

    @tool
    def lookup_order(order_id: str) -> str:
        """Look up an order."""
        return "shipped"

    g = StateGraph(MessagesState)
    g.add_node("tools", ToolNode([refund, lookup_order]))
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    assert node_tools(g.compile()) == {"tools": ("lookup_order", "refund")}


def test_node_tools_uses_the_tools_metadata_hint() -> None:
    graph = _FakeCompiled(_FakeDrawable({"tools": _FakeNode(metadata={"__tools": ["b", "a"]})}))
    assert node_tools(graph) == {"tools": ("a", "b")}


def test_node_tools_falls_back_when_the_hint_is_malformed() -> None:
    node = _FakeNode(metadata={"__tools": "x"}, data=_FakeToolNode({"x": object()}))
    bad_item = _FakeNode(metadata={"__tools": [1]}, data=_FakeToolNode({"y": object()}))
    graph = _FakeCompiled(_FakeDrawable({"t1": node, "t2": bad_item}))
    assert node_tools(graph) == {"t1": ("x",), "t2": ("y",)}


def test_node_tools_skips_nodes_without_tools() -> None:
    assert node_tools(build_linear()) == {}
