"""T157: commit-first judge mode — two-step grade with an independent check (fakes first).

Paid A/A against a live model is documented in docs/judge-commit-first.md and is not run here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lgcov.judges import KPI, NodeState, Verdict

CANNOT_ANSWER = "cannot answer from this input"


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, sort_keys=True)


def independent_contains_check(
    node_input: Mapping[str, object],
    node_output: Mapping[str, object],
    reference_key: str,
) -> str:
    """Pass when the output text contains the reference string from the input."""
    ref = node_input.get(reference_key)
    if ref is None or ref == "":
        return "fail"
    needle = str(ref)
    return "pass" if needle and needle in _text(node_output) else "fail"


def commit_request(node_input: NodeState, *, task: str) -> dict[str, object]:
    """Step 1 payload: takes no node output (so the blind step cannot be handed it)."""
    return {"task": task, "node_input": node_input}


@dataclass(frozen=True)
class AgreementRecord:
    kpi: str
    case: str
    side: str
    sample: int
    model: str
    mode: str  # commit_first | no_commit
    commit_sha256: str
    commit_matches_check: bool
    label: str
    check_label: str
    label_matches_check: bool

    @classmethod
    def from_raw(
        cls, kpi: str, case: str, side: str, sample: int, raw: Mapping[str, Any]
    ) -> AgreementRecord:
        return cls(
            kpi=kpi,
            case=case,
            side=side,
            sample=sample,
            model=str(raw.get("model", "fake")),
            mode=str(raw.get("mode", "")),
            commit_sha256=str(raw.get("commit_sha256", "")),
            commit_matches_check=bool(raw.get("commit_matches_check", False)),
            label=str(raw.get("label", "")),
            check_label=str(raw.get("check_label", "")),
            label_matches_check=bool(raw.get("label_matches_check", False)),
        )


@dataclass(frozen=True)
class AaRate:
    false_positives: int
    eligible: int  # judgments where the independent check says pass

    @property
    def rate(self) -> float:
        return 0.0 if self.eligible == 0 else self.false_positives / self.eligible


def aa_false_positive_rate(
    cases: Sequence[tuple[Mapping[str, object], Mapping[str, object]]],
    *,
    judge_label: Callable[[dict[str, object], dict[str, object], str], str],
    reference_key: str = "status",
) -> AaRate:
    """A/A FP plumbing: same I/O judged as base and as PR; FP = judge fail where check pass."""
    fp = eligible = 0
    for node_input, node_output in cases:
        inp, out = dict(node_input), dict(node_output)
        check = independent_contains_check(inp, out, reference_key)
        for side in ("base", "pr"):
            if check != "pass":
                continue
            eligible += 1
            if judge_label(inp, out, side) == "fail":
                fp += 1
    return AaRate(fp, eligible)


class CommitFirstFakeJudge:
    """Two-step fake: commit from input only, then grade against the commit (no network)."""

    def __init__(
        self,
        kpi: KPI,
        *,
        commit: Callable[[NodeState], str],
        reference_key: str,
        model: str = "fake",
        task: str | None = None,
    ) -> None:
        self.kpi = kpi
        self._commit = commit
        self.reference_key = reference_key
        self.model = model
        self.task = task or kpi.rubric

    def judge(self, node_input: NodeState, node_output: NodeState) -> Verdict:
        # Blind step: build with a function that takes no output.
        _ = commit_request(node_input, task=self.task)
        committed = self._commit(node_input)
        check_label = independent_contains_check(node_input, node_output, self.reference_key)
        if not committed or committed.strip().lower() == CANNOT_ANSWER:
            # no_commit: one-step grade against the check alone for the fake.
            label = check_label if check_label in self.kpi.labels else "fail"
            raw: dict[str, Any] = {
                "model": self.model,
                "mode": "no_commit",
                "commit_sha256": "",
                "commit_matches_check": False,
                "label": label,
                "check_label": check_label,
                "label_matches_check": label == check_label,
            }
            return Verdict(self.kpi, label, self.kpi.score_map[label], 1.0, raw)

        commit_sha = hashlib.sha256(committed.encode("utf-8")).hexdigest()
        commit_matches = committed in _text(node_input) or committed == str(
            node_input.get(self.reference_key, "")
        )
        # Grade: pass iff the candidate output agrees with the committed answer.
        agrees = committed in _text(node_output)
        label = "pass" if agrees else "fail"
        if label not in self.kpi.labels:
            label = min(self.kpi.labels, key=self.kpi.score_map.__getitem__)
        raw = {
            "model": self.model,
            "mode": "commit_first",
            "commit": committed,
            "commit_sha256": commit_sha,
            "commit_matches_check": bool(commit_matches),
            "label": label,
            "check_label": check_label,
            "label_matches_check": label == check_label,
        }
        return Verdict(self.kpi, label, self.kpi.score_map[label], 1.0, raw)
