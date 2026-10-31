"""T104: graph lint, warnings only. What is wrong with a graph's shape before replay runs
it: nodes nothing reaches, routers and edges that name nodes that don't exist, nodes no
recorded run reached, and (info) nodes no KPI names. Pure, except `routers_of`, which
reads a compiled graph.

LangGraph's compile() already refuses a router or an edge naming a node that doesn't
exist, so on a compiled graph those two checks only catch a snapshot that skipped it.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langgraph.graph import END, START

from lgcov.snapshot import GraphSnapshot

Severity = Literal["warn", "info"]


@dataclass(frozen=True)
class Finding:
    severity: Severity
    node: str  # the node, or the router, the fix goes in
    message: str
    hint: str  # the fix, in one line

    def line(self) -> str:
        return f"{self.severity} `{self.node}`: {self.message}. Fix: {self.hint}."


@dataclass(frozen=True)
class Router:
    """A conditional edge's router: its name (LangGraph names it after the function), the
    node it routes from, and the nodes its mapping names. None: it has no mapping and no
    Literal return type, so it may pick any node."""

    name: str
    source: str
    targets: tuple[str, ...] | None


# Any: a compiled graph from user code; see cassette.record for why it stays untyped.
def routers_of(graph: Any) -> tuple[Router, ...]:
    branches = getattr(getattr(graph, "builder", None), "branches", None) or {}
    found = []
    for source, per_source in branches.items():
        for name, branch in per_source.items():
            ends = getattr(branch, "ends", None)
            targets = None if ends is None else tuple(str(t) for t in ends.values())
            found.append(Router(str(name), str(source), targets))
    return tuple(sorted(found, key=lambda r: (r.source, r.name)))


def _reachable(snapshot: GraphSnapshot, routers: Sequence[Router]) -> set[str]:
    nodes = snapshot.nodes | {START, END}
    out: dict[str, set[str]] = {}
    for e in snapshot.edges:
        out.setdefault(e.source, set()).add(e.target)
    for r in routers:  # get_graph() draws a router with no mapping as an edge to END
        out.setdefault(r.source, set()).update(nodes if r.targets is None else r.targets)
    seen, todo = {START}, [START]
    while todo:
        for nxt in out.get(todo.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def _names(nodes: Sequence[str]) -> str:
    return ", ".join(f"`{n}`" for n in nodes)


_UNREACHABLE = (
    "add an edge into it or remove it (if `Command(goto=...)` reaches it, annotate that "
    "node's return type as `Command[Literal[...]]`)"
)


def lint(
    snapshot: GraphSnapshot,
    routers: Sequence[Router] = (),
    hit: Collection[str] | None = None,
    kpi_nodes: Collection[str] | None = None,
) -> list[Finding]:
    """Warnings, then info; each check's findings by name. `hit`: the nodes recorded runs
    reached (None: no cassettes, no check); `kpi_nodes`: the nodes the taxonomy's KPIs
    name (None: no taxonomy, no check)."""
    known = snapshot.nodes | {START, END}
    nodes = snapshot.nodes - {START, END}
    unreachable = sorted(nodes - _reachable(snapshot, routers))
    found = [Finding("warn", n, "unreachable from START", _UNREACHABLE) for n in unreachable]
    for r in sorted(routers, key=lambda r: (r.name, r.source)):
        missing = [t for t in r.targets or () if t not in known]
        if missing:
            message = f"router on `{r.source}` maps to nodes that don't exist: {_names(missing)}"
            found.append(Finding("warn", r.name, message, "add them, or map to nodes that exist"))
    for e in snapshot.sorted_edges():
        gone = [n for n in (e.source, e.target) if n not in known]
        if gone:
            where = "ends" if e.target in gone else "starts"
            message = f"edge `{e.source} → {e.target}` {where} at a node that doesn't exist"
            node = e.source if e.source in known else e.target
            found.append(Finding("warn", node, message, f"add {_names(gone)}, or remove the edge"))
    if hit is not None:  # an unreachable node already says why no run reached it
        missed = sorted(nodes - set(hit) - set(unreachable))
        hint = "record a case that reaches it (`lgcov record`)"
        found += [Finding("warn", n, "no recorded run reached it", hint) for n in missed]
    if kpi_nodes is not None:
        hint = "add one if its output matters"
        found += [
            Finding("info", n, "no KPI in the taxonomy names it", hint)
            for n in sorted(nodes - set(kpi_nodes))
        ]
    return found


def summary(findings: Sequence[Finding]) -> str:
    """The line for the PR comment and the end of `lgcov lint`: "🧹 Lint: 2 warnings"."""
    warns = sum(f.severity == "warn" for f in findings)
    infos = len(findings) - warns
    line = f"🧹 Lint: {warns} warning{'' if warns == 1 else 's'}"
    return line + (f" · {infos} info" if infos else "")
