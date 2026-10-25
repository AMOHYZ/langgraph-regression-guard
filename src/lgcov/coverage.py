"""Node/edge coverage of a graph snapshot from execution traces (T052)."""

from __future__ import annotations

from dataclasses import dataclass

from lgcov.snapshot import Edge, GraphSnapshot


@dataclass(frozen=True)
class CoverageReport:
    node_pct: float
    edge_pct: float
    unhit_nodes: list[str]
    unhit_edges: list[Edge]


def coverage(snapshot: GraphSnapshot, runs: list[list[str]]) -> CoverageReport:
    """Coverage of `snapshot` given per-run traces of visited node names.

    An edge counts as hit when some run visits its source immediately
    followed by its target. Traces carry node names only, so __start__ and
    __end__ (and their edges) can never be hit in v0.
    """
    visited = {n for run in runs for n in run}
    # strict=False on purpose: run and run[1:] differ in length by one.
    hit_pairs = {(a, b) for run in runs for a, b in zip(run, run[1:], strict=False)}
    unhit_nodes = sorted(snapshot.nodes - visited)
    unhit_edges = sorted(e for e in snapshot.edges if (e.source, e.target) not in hit_pairs)
    return CoverageReport(
        node_pct=_pct(len(snapshot.nodes) - len(unhit_nodes), len(snapshot.nodes)),
        edge_pct=_pct(len(snapshot.edges) - len(unhit_edges), len(snapshot.edges)),
        unhit_nodes=unhit_nodes,
        unhit_edges=unhit_edges,
    )


def _pct(hit: int, total: int) -> float:
    return 100.0 * hit / total if total else 100.0
