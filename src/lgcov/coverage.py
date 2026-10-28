"""Node/edge coverage of a graph snapshot from execution traces (T052, T083)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lgcov.snapshot import Edge, GraphSnapshot


@dataclass(frozen=True)
class CoverageReport:
    node_pct: float
    edge_pct: float
    unhit_nodes: list[str]
    unhit_edges: list[Edge]

    def to_dict(self) -> dict[str, Any]:
        # Stable key order for the coverage.json artifact.
        return {
            "edge_pct": self.edge_pct,
            "node_pct": self.node_pct,
            "unhit_edges": [e.to_dict() for e in self.unhit_edges],
            "unhit_nodes": list(self.unhit_nodes),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CoverageReport:
        edges = [Edge.from_dict(e) for e in d["unhit_edges"]]
        return cls(
            node_pct=float(d["node_pct"]),
            edge_pct=float(d["edge_pct"]),
            unhit_nodes=[str(n) for n in d["unhit_nodes"]],
            unhit_edges=edges,
        )


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


def format_coverage_delta(current: float, baseline: float | None) -> str:
    """e.g. '75.0% (-25.0% vs main)' or '75.0% (no baseline)'."""
    if baseline is None:
        return f"{current:.1f}% (no baseline)"
    delta = current - baseline
    sign = "+" if delta >= 0 else ""
    return f"{current:.1f}% ({sign}{delta:.1f}% vs main)"


def write_coverage(path: Path | str, report: CoverageReport) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")


def load_coverage(path: Path | str) -> CoverageReport:
    # Any: raw JSON before validation
    raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: coverage.json must be a mapping")
    return CoverageReport.from_dict(raw)


def _pct(hit: int, total: int) -> float:
    return 100.0 * hit / total if total else 100.0
