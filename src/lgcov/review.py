from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

Action = Literal["accept", "reject", "override"]
ACTIONS: tuple[str, ...] = ("accept", "reject", "override")
# Node I/O payloads are arbitrary user state (same rationale as cassette.NodeIO).
Cassette = dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ReviewDecision:
    node: str
    action: Action
    reviewer: str
    reason: str = ""
    label: float | None = None  # human score; required for "override"
    # T063: the judge an override corrects and what it said, so `lgcov calibrate` can count
    # the override as a label. T102 moves the judge's band from these.
    kpi: str = ""
    judge_label: str = ""
    case_id: str = ""

    @classmethod
    def from_json(cls, raw: str) -> ReviewDecision:
        d = json.loads(raw)
        for key in ("node", "action", "reviewer"):
            if not d.get(key):
                raise ValueError(f"missing field: {key}")
        if d["action"] not in ACTIONS:
            raise ValueError(f"unknown action: {d['action']}")
        if d["action"] == "override" and d.get("label") is None:
            raise ValueError("override needs a label")
        return cls(
            node=d["node"],
            action=cast(Action, d["action"]),  # validated against ACTIONS above
            reviewer=d["reviewer"],
            reason=d.get("reason", ""),
            label=d.get("label"),
            kpi=d.get("kpi", ""),
            judge_label=d.get("judge_label", ""),
            case_id=d.get("case_id", ""),
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def apply_decision(
    decision: ReviewDecision,
    baseline: Cassette,
    candidate: Cassette,
    calibration_file: Path,
) -> Cassette:
    """Return the new baseline cassette after a human review decision."""
    if decision.action == "accept":
        updated = dict(baseline)
        updated[decision.node] = candidate[decision.node]  # re-record this node only
        return updated
    if decision.action == "override":
        example = {
            "node": decision.node,
            "output": candidate[decision.node]["output"],
            "human_label": decision.label,
            "reviewer": decision.reviewer,
            "reason": decision.reason,
            "kpi": decision.kpi,
            "judge_label": decision.judge_label,
            "case_id": decision.case_id,
        }
        with calibration_file.open("a") as f:
            f.write(json.dumps(example, sort_keys=True) + "\n")
    return baseline  # reject / override keep the baseline
