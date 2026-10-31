"""Node/edge coverage of a graph snapshot from execution traces (T052), its
coverage.json form, and the trend against main's (T083)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from lgcov.snapshot import Edge, GraphSnapshot

START, END = "__start__", "__end__"
COVERAGE_VERSION = 1


@dataclass(frozen=True)
class CoverageReport:
    node_pct: float
    edge_pct: float
    unhit_nodes: list[str]
    unhit_edges: list[Edge]


def _completed(run: list[str]) -> list[str]:
    """Traces carry only the nodes that ran; every completed run also entered at
    __start__ and left at __end__, so both (and the edges into and out of the run) count.
    """
    head = [] if run[:1] == [START] else [START]
    tail = [] if run[-1:] == [END] else [END]
    return [*head, *run, *tail]


def coverage(snapshot: GraphSnapshot, runs: list[list[str]]) -> CoverageReport:
    """Coverage of `snapshot` given per-run traces of visited node names.

    An edge counts as hit when some run visits its source immediately
    followed by its target.
    """
    runs = [_completed(run) for run in runs]
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


@dataclass(frozen=True)
class CoverageSnapshot:
    """One commit's coverage, as stored in the coverage.json artifact."""

    nodes_total: int
    nodes_hit: int
    edges_total: int
    edges_hit: int
    hit_nodes: tuple[str, ...]  # sorted; lets a delta tell "hit on main" from "new"
    never_hit_nodes: tuple[str, ...]
    never_hit_edges: tuple[Edge, ...]
    commit_sha: str = ""

    @property
    def node_pct(self) -> float:
        return _pct(self.nodes_hit, self.nodes_total)

    @property
    def edge_pct(self) -> float:
        return _pct(self.edges_hit, self.edges_total)

    def to_json(self) -> str:
        """Deterministic: sorted keys and lists, 2-space indent, trailing newline."""
        body = {
            "commit_sha": self.commit_sha,
            "edges_hit": self.edges_hit,
            "edges_total": self.edges_total,
            "hit_nodes": list(self.hit_nodes),
            "never_hit_edges": [e.to_dict() for e in self.never_hit_edges],
            "never_hit_nodes": list(self.never_hit_nodes),
            "nodes_hit": self.nodes_hit,
            "nodes_total": self.nodes_total,
            "version": COVERAGE_VERSION,
        }
        return json.dumps(body, indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> CoverageSnapshot:
        raw: object = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError("coverage.json must be a mapping")
        if raw.get("version") != COVERAGE_VERSION:
            raise ValueError(f"unsupported coverage.json version: {raw.get('version')!r}")
        try:
            return cls(
                nodes_total=int(raw["nodes_total"]),
                nodes_hit=int(raw["nodes_hit"]),
                edges_total=int(raw["edges_total"]),
                edges_hit=int(raw["edges_hit"]),
                hit_nodes=tuple(str(n) for n in raw["hit_nodes"]),
                never_hit_nodes=tuple(str(n) for n in raw["never_hit_nodes"]),
                never_hit_edges=tuple(Edge.from_dict(e) for e in raw["never_hit_edges"]),
                commit_sha=str(raw.get("commit_sha", "")),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed coverage.json: {exc!r}") from None


def snapshot_coverage(
    snapshot: GraphSnapshot, runs: list[list[str]], commit_sha: str = ""
) -> CoverageSnapshot:
    """T052's coverage of `runs`, with the counts and node lists coverage.json keeps."""
    rep = coverage(snapshot, runs)
    return CoverageSnapshot(
        nodes_total=len(snapshot.nodes),
        nodes_hit=len(snapshot.nodes) - len(rep.unhit_nodes),
        edges_total=len(snapshot.edges),
        edges_hit=len(snapshot.edges) - len(rep.unhit_edges),
        hit_nodes=tuple(sorted(snapshot.nodes - set(rep.unhit_nodes))),
        never_hit_nodes=tuple(rep.unhit_nodes),
        never_hit_edges=tuple(rep.unhit_edges),
        commit_sha=commit_sha,
    )


@dataclass(frozen=True)
class CoverageDelta:
    nodes_pp: float  # percentage points, head minus base
    edges_pp: float
    newly_uncovered: tuple[str, ...]  # hit on main, never hit here
    newly_covered: tuple[str, ...]  # never hit on main, hit here


def coverage_delta(base: CoverageSnapshot, head: CoverageSnapshot) -> CoverageDelta:
    return CoverageDelta(
        nodes_pp=head.node_pct - base.node_pct,
        edges_pp=head.edge_pct - base.edge_pct,
        newly_uncovered=tuple(sorted(set(base.hit_nodes) & set(head.never_hit_nodes))),
        newly_covered=tuple(sorted(set(base.never_hit_nodes) & set(head.hit_nodes))),
    )


def format_delta(pp: float) -> str:
    """'±0', '−25%' (a real minus sign) or '+12.5%', to one decimal place."""
    rounded = round(pp, 1)
    if rounded == 0:
        return "±0"
    return f"{'+' if rounded > 0 else '−'}{abs(rounded):g}%"


def _pct_text(pct: float) -> str:
    return f"{round(pct, 1):g}%"


def format_coverage_delta(current: float, baseline: float | None) -> str:
    """e.g. '75% (−25% vs main)', '60% (±0)' or '75% (no baseline yet)'."""
    if baseline is None:
        return f"{_pct_text(current)} (no baseline yet)"
    delta = format_delta(current - baseline)
    return f"{_pct_text(current)} ({delta if delta == '±0' else delta + ' vs main'})"


def format_coverage_line(head: CoverageSnapshot, base: CoverageSnapshot | None) -> str:
    """The report's one coverage line, e.g. 'Nodes 75% (−25% vs main) · Edges 60% (±0)'."""
    if base is None:
        return (
            f"Nodes {_pct_text(head.node_pct)} · Edges {_pct_text(head.edge_pct)} (no baseline yet)"
        )
    nodes = format_coverage_delta(head.node_pct, base.node_pct)
    edges = format_coverage_delta(head.edge_pct, base.edge_pct)
    return f"Nodes {nodes} · Edges {edges}"


def _pct(hit: int, total: int) -> float:
    return 100.0 * hit / total if total else 100.0
