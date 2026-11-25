"""T234: cumulative loss vs the kept reference round (option 2). No summing across rounds."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import yaml

from lgcov.cumulative_loss import loss_vs_reference
from lgcov.gate_round import (
    Draft,
    GateNow,
    JudgeLock,
    Reference,
    Round,
    lock_round,
    parse_draft,
    reference_copy_problems,
    round_digest,
    round_to_yaml,
)

A = "a" * 40
JUDGE = JudgeLock("jev-1", "0.1.0", "c" * 64)
NOW = GateNow(
    case_ids=("id-1", "id-2"),
    judge=JUDGE,
    margins={"grounded": 0.05},
    alpha=0.05,
    power=0.8,
    k_gate=1,
)
DRAFT: dict[str, object] = {
    "version": 1,
    "gate_cases": {"path": "cassettes/baseline.json"},
    "kpis": [{"name": "grounded", "tags": ["safety"]}],
    "delta_1": 0.0,
    "prior_rho": 0.6,
    "candidates": [A],
    "stop_rules": {"max_cases": 800},
}


def _round1() -> Round:
    return lock_round(parse_draft(DRAFT), NOW, 0)


def test_breach_beyond_margin_is_block_exit_1() -> None:
    """Loss beyond m vs the reference → BLOCK, exit 1. No summing of intermediate rounds."""
    reference = [[1.0]] * 20
    candidate = [[0.0]] * 20  # every case fails vs the kept reference
    loss = loss_vs_reference(reference, candidate, margin=0.05)
    assert loss.decision == "BLOCK"
    assert loss.state == "blocked"
    assert loss.exit_code == 1


def test_inconclusive_vs_reference_is_needs_human() -> None:
    """Too few identical cases to clear the margin → needs_human, existing exit 0."""
    same = [[1.0], [0.0], [1.0], [1.0], [0.0]]
    loss = loss_vs_reference(same, same, margin=0.05)
    assert loss.decision == "INCONCLUSIVE"
    assert loss.state == "needs_human"
    assert loss.exit_code == 0


def test_ship_vs_reference_is_ok() -> None:
    """Identical sides with enough cases to clear m → ok / exit 0."""
    same = [[1.0], [0.0]] * 40  # 80 cases: no_drop_bound under 0.05
    loss = loss_vs_reference(same, same, margin=0.05)
    assert loss.decision == "SHIP"
    assert loss.state == "ok"
    assert loss.exit_code == 0


def test_missing_reference_path_is_a_problem() -> None:
    ref = Reference(1, "d" * 64, path="rounds/missing.yaml")
    problems = reference_copy_problems(ref, Path("/tmp"))
    assert problems
    assert "missing" in problems[0]


def test_hash_mismatch_on_kept_copy_is_a_problem(tmp_path: Path) -> None:
    first = _round1()
    kept = tmp_path / "ref.yaml"
    kept.write_text(round_to_yaml(first), encoding="utf-8")
    ref = Reference(1, "e" * 64, path="ref.yaml")
    problems = reference_copy_problems(ref, tmp_path)
    assert problems
    assert "digest" in problems[0]


def test_happy_path_kept_reference_rehashes_clean(tmp_path: Path) -> None:
    first = _round1()
    (tmp_path / "rounds").mkdir()
    kept = tmp_path / "rounds" / "1.yaml"
    kept.write_text(round_to_yaml(first), encoding="utf-8")
    ref = Reference(1, first.digest, path="rounds/1.yaml")
    assert reference_copy_problems(ref, tmp_path) == []


def test_reference_path_omitted_keeps_digest_stable() -> None:
    """A key a round doesn't have is left out of its digest (pre-path rounds stay pinned)."""
    first = _round1()
    locked: dict[str, object] = yaml.safe_load(round_to_yaml(first))
    second = lock_round(parse_draft(locked), NOW, 0)
    assert second.reference == Reference(1, first.digest, None)
    assert "path" not in yaml.safe_load(round_to_yaml(second))["reference"]


def test_reference_path_participates_in_digest_when_set() -> None:
    first = _round1()
    locked: dict[str, object] = yaml.safe_load(round_to_yaml(first))
    draft = parse_draft({**locked, "reference_path": "rounds/1.yaml"})
    assert isinstance(draft, Draft)
    second = lock_round(draft, NOW, 0)
    assert second.reference == Reference(1, first.digest, "rounds/1.yaml")
    without_path = replace(second, reference=Reference(1, first.digest, None))
    assert round_digest(second) != round_digest(without_path)
