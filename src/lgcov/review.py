from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
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


@dataclass(frozen=True)
class Changes:
    """T186: what makes a folder of the PR's the base's again, by each file's path under it: the
    files to write, with the base's bytes, and the files to remove."""

    write: Mapping[str, bytes]
    remove: tuple[str, ...]

    @property
    def empty(self) -> bool:
        return not self.write and not self.remove


def reject_changes(pr: Mapping[str, bytes], base: Mapping[str, bytes]) -> Changes:
    """T186: `reject` on cassette_dir: every file the base has, as the base has it, and none that
    it doesn't. Each side maps a file's path under the folder to its bytes."""
    write = {path: data for path, data in sorted(base.items()) if pr.get(path) != data}
    return Changes(write, tuple(sorted(set(pr) - set(base))))


_ACTION_FILES = ("action.yml", "action.yaml")


def unwritable(path: str) -> str | None:
    """T186: why no review command may write this path, given from the repo's root; None when one
    may. Nothing outside the repo, nothing in a .github folder and no file named action.yml or
    action.yaml, at any depth (docs/review.md)."""
    pure = PurePosixPath(path)
    if not pure.parts or pure.is_absolute() or ".." in pure.parts:
        return f"`{path}` isn't a path inside the repo"
    if ".github" in pure.parts:
        return f"`{path}` is in a .github folder"
    if pure.name in _ACTION_FILES:
        return f"`{path}` is an action file"
    return None
