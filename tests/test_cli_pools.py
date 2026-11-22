"""T124: `lgcov pools check` exit codes and the base-ref lineage / pools_edited path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from lgcov.cli import lgcov_main
from lgcov.gate_round import JudgeLock
from lgcov.pools import LineageEntry, lineage_to_json, pools_paths_edited
from lgcov.verdict_actions import Evidence, decide_action

JUDGE = JudgeLock("jev-1", "0.1.0", "c" * 64)


def _case(folder: Path, case_id: str, text: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{case_id}.json").write_text(
        json.dumps({"id": case_id, "text": text}), encoding="utf-8"
    )


def _round(path: Path, judge: JudgeLock = JUDGE) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "round": 1,
                "gate_cases": {"path": "cassettes/baseline.json", "sha256": "a" * 64},
                "judge": {
                    "model": judge.model,
                    "version": judge.version,
                    "prompt_sha256": judge.prompt_sha256,
                },
                "kpis": [{"name": "respond_runs", "tags": ["flow"], "margin": 0.05}],
                "alpha": 0.05,
                "power": 0.8,
                "delta_1": 0.0,
                "prior_rho": 0.6,
                "k_gate": 1,
                "candidates": ["a" * 40],
                "stop_rules": {},
                "exposure_floor": 0,
                "digest": "b" * 64,
            }
        ),
        encoding="utf-8",
    )


def test_pools_check_refuses_id_overlap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _case(tmp_path / "cases/optimiser", "shared", "Where is my order?")
    _case(tmp_path / "cases/gate", "shared", "Where is my order?")
    _round(tmp_path / "lgcov-round.yaml")
    assert lgcov_main(["pools", "check"]) == 2


def test_pools_check_passes_a_clean_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _case(tmp_path / "cases/optimiser", "opt-1", "Where is my order?")
    _case(tmp_path / "cases/gate", "gate-1", "What is the refund policy?")
    _round(tmp_path / "lgcov-round.yaml")
    (tmp_path / "lineage.json").write_text(
        lineage_to_json(
            {
                "opt-1": LineageEntry("optimiser", (JUDGE.id,)),
                "gate-1": LineageEntry("gate"),
            }
        ),
        encoding="utf-8",
    )
    assert lgcov_main(["pools", "check"]) == 0


def test_pools_check_reads_lineage_from_policy_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    pr, base = tmp_path / "pr", tmp_path / "base"
    pr.mkdir()
    base.mkdir()
    monkeypatch.chdir(pr)
    _case(pr / "cases/gate", "fit-me", "Track this package")
    _round(base / "lgcov-round.yaml")
    (base / "lineage.json").write_text(
        lineage_to_json({"fit-me": LineageEntry("gate", (JUDGE.id,))}),
        encoding="utf-8",
    )
    # PR has no lineage of its own; check still sees the base's judges_fitted.
    assert lgcov_main(["pools", "check", "--policy-dir", str(base)]) == 2


def test_pools_or_lineage_edited_versus_base(tmp_path: Path) -> None:
    pr, base = tmp_path / "pr", tmp_path / "base"
    (base / "cases/gate").mkdir(parents=True)
    (pr / "cases/gate").mkdir(parents=True)
    (base / "lineage.json").write_text('{"version": 1, "cases": {}}\n', encoding="utf-8")
    (pr / "lineage.json").write_text(lineage_to_json({"x": LineageEntry("gate")}), encoding="utf-8")
    assert pools_paths_edited(pr, base)
    assert not pools_paths_edited(base, base)
    action = decide_action(Evidence(pools_edited=True, statistical="SHIP", k_gate=1))
    assert action.verdict == "needs_human"
    assert action.state == "pools_edited"
