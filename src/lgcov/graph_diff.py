"""Compare two graph snapshots and report what changed."""

from __future__ import annotations

from dataclasses import dataclass

# Single source of truth for snapshot shapes lives in snapshot.py; graph_diff
# adds only the diffing logic on top of it (Edge carries the conditional flag).
from lgcov.snapshot import Edge, GraphSnapshot

# Explicit re-export: existing callers import GraphSnapshot/Edge from graph_diff.
__all__ = ["Edge", "GraphSnapshot", "GraphDiff", "diff_graphs"]


@dataclass(frozen=True)
class GraphDiff:
    added_nodes: frozenset[str]
    removed_nodes: frozenset[str]
    added_edges: frozenset[Edge]
    removed_edges: frozenset[Edge]

    @property
    def is_empty(self) -> bool:
        return not (
            self.added_nodes or self.removed_nodes or self.added_edges or self.removed_edges
        )


def diff_graphs(old: GraphSnapshot, new: GraphSnapshot) -> GraphDiff:
    return GraphDiff(
        added_nodes=new.nodes - old.nodes,
        removed_nodes=old.nodes - new.nodes,
        added_edges=new.edges - old.edges,
        removed_edges=old.edges - new.edges,
    )
