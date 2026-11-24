"""T129: export a change record as JSON + markdown for a PR.

Maps fields to EU AI Act Art. 17(1)(a)/(d)/(k) and provisionally ISO/IEC 42001 8.1
as **evidence only** — never a compliance claim.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from lgcov.adoption import ChangeRecord

# Evidence mapping only (never a compliance claim). Phrases avoid banned words.
ART_17_MAP: tuple[str, ...] = (
    "Art. 17(1)(a): evidence of the change description (diff summary).",
    "Art. 17(1)(d): evidence of quality control steps (KPIs + statistical verdict).",
    "Art. 17(1)(k): evidence of post-change monitoring handoff (approver + adoption state).",
)
ISO_42001_MAP: tuple[str, ...] = (
    "ISO/IEC 42001 8.1 (provisional): evidence of operational planning and control "
    "for this change (affected nodes, KPIs, verdict, approver, state).",
)


@dataclass(frozen=True)
class ExportPayload:
    pr: int
    diff: str
    affected_nodes: tuple[str, ...]
    kpis: tuple[str, ...]
    verdict: str | None
    approver: str | None
    state: str
    base_sha: str
    candidate_sha: str
    history: tuple[str, ...]

    @classmethod
    def from_record(
        cls,
        record: ChangeRecord,
        *,
        diff_summary: str,
        affected_nodes: tuple[str, ...],
        kpis: tuple[str, ...],
    ) -> ExportPayload:
        approver = None if record.approval is None else record.approval.approver
        return cls(
            pr=record.pr,
            diff=diff_summary,
            affected_nodes=affected_nodes,
            kpis=kpis,
            verdict=record.statistical_verdict,
            approver=approver,
            state=record.state,
            base_sha=record.base_sha,
            candidate_sha=record.candidate_sha,
            history=tuple(record.history),
        )


def export_json(path: Path, payload: ExportPayload) -> None:
    path.write_text(json.dumps(asdict(payload), indent=2) + "\n", encoding="utf-8")


def export_markdown(path: Path, payload: ExportPayload) -> None:
    nodes = ", ".join(f"`{n}`" for n in payload.affected_nodes) or "(none)"
    kpis = ", ".join(f"`{k}`" for k in payload.kpis) or "(none)"
    lines = [
        f"# Change record — PR #{payload.pr}",
        "",
        "This file is **evidence** for the change below. It is not a compliance claim.",
        "",
        "## Diff",
        "",
        payload.diff or "(no summary)",
        "",
        "## Affected nodes",
        "",
        nodes,
        "",
        "## KPIs",
        "",
        kpis,
        "",
        "## Verdict",
        "",
        payload.verdict or "(none)",
        "",
        "## Approver",
        "",
        payload.approver or "(none)",
        "",
        "## State",
        "",
        f"`{payload.state}` (history: {', '.join(payload.history)})",
        "",
        "## Evidence mapping (not a claim of conformity)",
        "",
        *[f"- {line}" for line in ART_17_MAP],
        *[f"- {line}" for line in ISO_42001_MAP],
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
