"""Graph snapshots of compiled LangGraph graphs (T050 adapter, T051 JSON round-trip).

A GraphSnapshot is the stable, comparable shape of a graph: node ids plus edges
(source, target, conditional). It is what graph_diff compares and what the PR
report renders.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

SCHEMA_VERSION = 1


class _CompiledGraph(Protocol):
    # Any: langgraph's drawable graph has no stable public type across versions.
    def get_graph(self) -> Any: ...


@dataclass(frozen=True, order=True)
class Edge:
    source: str
    target: str
    conditional: bool = False

    # dict[str, Any] here and in from_dict: values come straight from JSON.
    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "conditional": self.conditional}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Edge:
        return cls(str(d["source"]), str(d["target"]), bool(d.get("conditional", False)))


@dataclass(frozen=True)
class GraphSnapshot:
    nodes: frozenset[str]
    edges: frozenset[Edge] = field(default_factory=frozenset)

    def sorted_edges(self) -> list[Edge]:
        return sorted(self.edges, key=lambda e: (e.source, e.target, e.conditional))

    # ---- T051: JSON round-trip ------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "nodes": sorted(self.nodes),
            "edges": [e.to_dict() for e in self.sorted_edges()],
        }

    def to_json(self) -> str:
        """Deterministic JSON: sorted nodes and edges, 2-space indent, trailing newline."""
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphSnapshot:
        version = d.get("version")
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported snapshot version: {version!r}")
        nodes = frozenset(str(n) for n in d["nodes"])
        edges = frozenset(Edge.from_dict(e) for e in d["edges"])
        for e in edges:
            for end in (e.source, e.target):
                if end not in nodes:
                    raise ValueError(f"edge {e.source}->{e.target} references unknown node {end!r}")
        return cls(nodes=nodes, edges=edges)

    @classmethod
    def from_json(cls, text: str) -> GraphSnapshot:
        return cls.from_dict(json.loads(text))


# ---- T050: adapter ----------------------------------------------------------


def snapshot_from_compiled(compiled: _CompiledGraph) -> GraphSnapshot:
    """Build a GraphSnapshot from a compiled LangGraph graph via get_graph().

    Keeps __start__/__end__ so entry and exit edges show up in diffs and coverage.
    """
    graph = compiled.get_graph()
    nodes = frozenset(str(node_id) for node_id in graph.nodes)
    edges = frozenset(Edge(str(e.source), str(e.target), bool(e.conditional)) for e in graph.edges)
    return GraphSnapshot(nodes=nodes, edges=edges)
