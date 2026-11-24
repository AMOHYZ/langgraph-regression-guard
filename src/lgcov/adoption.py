"""T155: adoption path state machine and approve-bind (distinct from ProposeState).

eligible → approved → baseline_updated → merged → deployed.
T155-A: enum + transitions + change-record stub.
T155-B: approve binds base/candidate SHAs + hashes; stale detection; risk acceptance
on --override (statistical verdict unchanged); cassette accept needs a fresh approval
when a change record is in play.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, get_args

AdoptionState = Literal["eligible", "approved", "baseline_updated", "merged", "deployed"]

ADOPTION_ORDER: tuple[AdoptionState, ...] = get_args(AdoptionState)


class IllegalTransition(ValueError):
    """A transition that skips or reverses the adoption path."""


class StaleApproval(ValueError):
    """Approval no longer matches the base/candidate SHAs or content hashes."""


def transition(current: AdoptionState, nxt: AdoptionState) -> AdoptionState:
    """Advance one step along ADOPTION_ORDER; anything else raises IllegalTransition."""
    try:
        i = ADOPTION_ORDER.index(current)
        j = ADOPTION_ORDER.index(nxt)
    except ValueError as exc:
        raise IllegalTransition(f"unknown adoption state in {current!r} → {nxt!r}") from exc
    if j != i + 1:
        raise IllegalTransition(f"illegal adoption transition {current!r} → {nxt!r}")
    return nxt


@dataclass(frozen=True)
class Approval:
    """T155-B: approval bound to base/candidate SHAs and content hashes."""

    base_sha: str
    candidate_sha: str
    base_hash: str
    candidate_hash: str
    approver: str
    risk_acceptance: str | None = None  # set when --override; verdict stays statistical


@dataclass(frozen=True)
class ChangeRecord:
    """Change record written on each transition (T155-A stub; T155-B binds approval)."""

    pr: int
    base_sha: str
    candidate_sha: str
    state: AdoptionState = "eligible"
    history: tuple[AdoptionState, ...] = field(default_factory=lambda: ("eligible",))
    approval: Approval | None = None
    statistical_verdict: str | None = None  # unchanged by risk-acceptance override

    @classmethod
    def new(cls, *, pr: int, base_sha: str, candidate_sha: str) -> ChangeRecord:
        return cls(pr=pr, base_sha=base_sha, candidate_sha=candidate_sha)

    def advance(self, nxt: AdoptionState) -> ChangeRecord:
        state = transition(self.state, nxt)
        return ChangeRecord(
            pr=self.pr,
            base_sha=self.base_sha,
            candidate_sha=self.candidate_sha,
            state=state,
            history=(*self.history, state),
            approval=self.approval,
            statistical_verdict=self.statistical_verdict,
        )


def approve_bind(
    record: ChangeRecord,
    *,
    base_sha: str,
    candidate_sha: str,
    base_hash: str,
    candidate_hash: str,
    approver: str,
    override: bool = False,
    note: str = "",
    statistical_verdict: str | None = None,
) -> ChangeRecord:
    """Bind approval to SHAs+hashes and advance eligible → approved.

    `--override` records risk acceptance only; `statistical_verdict` is stored unchanged.
    """
    if record.state != "eligible":
        raise IllegalTransition(f"approve requires eligible, got {record.state!r}")
    if (base_sha, candidate_sha) != (record.base_sha, record.candidate_sha):
        raise StaleApproval(
            "approve SHAs do not match the change record's base/candidate "
            f"(record {record.base_sha[:7]}…/{record.candidate_sha[:7]}…, "
            f"got {base_sha[:7]}…/{candidate_sha[:7]}…)"
        )
    approval = Approval(
        base_sha=base_sha,
        candidate_sha=candidate_sha,
        base_hash=base_hash,
        candidate_hash=candidate_hash,
        approver=approver,
        risk_acceptance=note if override else None,
    )
    advanced = record.advance("approved")
    return ChangeRecord(
        pr=advanced.pr,
        base_sha=advanced.base_sha,
        candidate_sha=advanced.candidate_sha,
        state=advanced.state,
        history=advanced.history,
        approval=approval,
        statistical_verdict=statistical_verdict,
    )


def require_fresh_approval(
    record: ChangeRecord,
    *,
    base_sha: str,
    candidate_sha: str,
    base_hash: str,
    candidate_hash: str,
) -> None:
    """Raise StaleApproval unless record has a matching non-stale approval."""
    if record.approval is None or record.state == "eligible":
        raise StaleApproval("no valid approval on the change record")
    a = record.approval
    if (a.base_sha, a.candidate_sha) != (base_sha, candidate_sha):
        raise StaleApproval("approval is stale: base/candidate SHA mismatch")
    if (a.base_hash, a.candidate_hash) != (base_hash, candidate_hash):
        raise StaleApproval("approval is stale: content hash mismatch")


def save_change_record(path: Path, record: ChangeRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    approval = None
    if record.approval is not None:
        a = record.approval
        approval = {
            "base_sha": a.base_sha,
            "candidate_sha": a.candidate_sha,
            "base_hash": a.base_hash,
            "candidate_hash": a.candidate_hash,
            "approver": a.approver,
            "risk_acceptance": a.risk_acceptance,
        }
    payload = {
        "pr": record.pr,
        "base_sha": record.base_sha,
        "candidate_sha": record.candidate_sha,
        "state": record.state,
        "history": list(record.history),
        "approval": approval,
        "statistical_verdict": record.statistical_verdict,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_change_record(path: Path) -> ChangeRecord:
    raw = json.loads(path.read_text(encoding="utf-8"))
    approval = None
    if isinstance(raw.get("approval"), dict):
        a = raw["approval"]
        approval = Approval(
            base_sha=str(a["base_sha"]),
            candidate_sha=str(a["candidate_sha"]),
            base_hash=str(a["base_hash"]),
            candidate_hash=str(a["candidate_hash"]),
            approver=str(a["approver"]),
            risk_acceptance=(
                None if a.get("risk_acceptance") is None else str(a["risk_acceptance"])
            ),
        )
    raw_history = raw.get("history") or ["eligible"]
    if not isinstance(raw_history, list) or not all(isinstance(s, str) for s in raw_history):
        raise ValueError("change record: history must be a list of states")
    state = str(raw["state"])
    if state not in ADOPTION_ORDER:
        raise ValueError(f"change record: unknown state {state!r}")
    steps: list[AdoptionState] = []
    for step in raw_history:
        if step not in ADOPTION_ORDER:
            raise ValueError(f"change record: unknown history state {step!r}")
        steps.append(step)
    return ChangeRecord(
        pr=int(raw["pr"]),
        base_sha=str(raw["base_sha"]),
        candidate_sha=str(raw["candidate_sha"]),
        state=state,
        history=tuple(steps),
        approval=approval,
        statistical_verdict=(
            None if raw.get("statistical_verdict") is None else str(raw["statistical_verdict"])
        ),
    )
