"""T053: render graph diff + coverage + judge verdicts as PR-comment markdown.

Pure and deterministic (sorted rows, fixed number formats, no timestamps) so
the GitHub Action (T039) can post it and tests can compare it byte for byte.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from lgcov.coverage import CoverageReport
from lgcov.graph_diff import GraphDiff
from lgcov.judge_gate import GateOutcome
from lgcov.snapshot import Edge

INFRA_NOTE = "harness error, not a regression"
_ICONS: dict[GateOutcome, str] = {
    "pass": "🟢",
    "fail": "🔴",
    "needs_human": "🟡",
    "infra_error": "⚠️",
}


@dataclass(frozen=True)
class KpiResult:
    """One KPI's verdict_gate outcome, as shown in the report."""

    kpi: str
    outcome: GateOutcome
    confidence: float | None = None  # None when the judge never answered
    note: str = ""


def render_pr_report(
    diff: GraphDiff, coverage: CoverageReport, verdicts: Sequence[KpiResult]
) -> str:
    lines = [_header(verdicts), ""]
    lines += ["### Graph changes", "", *_graph_changes(diff), ""]
    lines += ["### Coverage", "", *_coverage(coverage), ""]
    lines += ["### Judge verdicts", "", *_verdicts(verdicts), ""]
    lines += [
        "---",
        "Reviewer: reply `accept`, `reject` or `override` per node to update the baseline (L3).",
    ]
    return "\n".join(lines) + "\n"


def _header(verdicts: Sequence[KpiResult]) -> str:
    outcomes = {v.outcome for v in verdicts}
    if outcomes & {"fail", "infra_error"}:
        return "## 🔴 lgcov: regression detected"
    if "needs_human" in outcomes:
        return "## 🟡 lgcov: needs human review"
    return "## 🟢 lgcov: no regressions"


def _edge(e: Edge) -> str:
    return f"`{e.source} → {e.target}`" + (" (conditional)" if e.conditional else "")


def _graph_changes(diff: GraphDiff) -> list[str]:
    if diff.is_empty:
        return ["No graph changes"]
    rows = [("added", "node", f"`{n}`") for n in sorted(diff.added_nodes)]
    rows += [("removed", "node", f"`{n}`") for n in sorted(diff.removed_nodes)]
    rows += [("added", "edge", _edge(e)) for e in sorted(diff.added_edges)]
    rows += [("removed", "edge", _edge(e)) for e in sorted(diff.removed_edges)]
    return [
        "| change | kind | item |",
        "|---|---|---|",
        *(f"| {c} | {k} | {i} |" for c, k, i in rows),
    ]


def _coverage(cov: CoverageReport) -> list[str]:
    lines = [
        "| | hit |",
        "|---|---|",
        f"| nodes | {cov.node_pct:.1f}% |",
        f"| edges | {cov.edge_pct:.1f}% |",
        "",
    ]
    if not cov.unhit_nodes and not cov.unhit_edges:
        return [*lines, "All nodes and edges hit."]
    summary = f"Never hit: {len(cov.unhit_nodes)} nodes, {len(cov.unhit_edges)} edges"
    return [
        *lines,
        "<details>",
        f"<summary>{summary}</summary>",
        "",
        *(f"- node `{n}`" for n in sorted(cov.unhit_nodes)),
        *(f"- edge {_edge(e)}" for e in sorted(cov.unhit_edges)),
        "",
        "</details>",
    ]


def _verdicts(verdicts: Sequence[KpiResult]) -> list[str]:
    if not verdicts:
        return ["No judge verdicts"]
    rows = []
    for v in sorted(verdicts, key=lambda v: v.kpi):
        conf = "—" if v.confidence is None else f"{v.confidence:.2f}"
        note = INFRA_NOTE if v.outcome == "infra_error" else v.note.replace("|", "\\|")
        rows.append(f"| {v.kpi} | {_ICONS[v.outcome]} {v.outcome} | {conf} | {note or '—'} |")
    return ["| KPI | verdict | confidence | note |", "|---|---|---|---|", *rows]
