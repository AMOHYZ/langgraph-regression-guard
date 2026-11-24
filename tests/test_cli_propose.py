"""T120: `lgcov propose` CLI. `lgcov guard` stays lock/check (exit 0/2 only)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from lgcov.cli import lgcov_main

FIXTURES = Path(__file__).parent / "fixtures"
DEMO_GRAPH = Path(__file__).resolve().parents[1] / "examples" / "demo_agent" / "graph.py"
DEMO_BASELINE = (
    Path(__file__).resolve().parents[1] / "examples" / "demo_agent" / "cassettes" / "baseline.json"
)
SCHEMA = Path(__file__).resolve().parents[1] / "docs" / "guard-schema.json"
FRIENDLY = "You are a friendly, detailed support agent."
CONCISE = "You are a concise support agent."
CONFIG = (
    "graph_entrypoint: graph.py:graph\n"
    "cassette_dir: cassettes\n"
    "taxonomy_path: taxonomy.yaml\n"
    "jev_model: jev-test-1\n"
)
BASELINE = "cassettes/baseline.json"
ROUND = "lgcov-round.yaml"
LEDGER = "exposure.json"
TAXONOMY = "taxonomy.yaml"
OTHER_SHA = "b" * 40


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return done.stdout.decode().strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(path: Path, monkeypatch: pytest.MonkeyPatch, graph: str | None = None) -> str:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "lgcov test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    path.mkdir(exist_ok=True)
    monkeypatch.chdir(path)
    Path("lgcov.yaml").write_text(CONFIG, encoding="utf-8")
    shutil.copy(FIXTURES / "demo_taxonomy.yaml", TAXONOMY)
    Path("cassettes").mkdir()
    shutil.copy(DEMO_BASELINE, BASELINE)
    Path("graph.py").write_text(graph or DEMO_GRAPH.read_text(encoding="utf-8"), encoding="utf-8")
    _git(path, "init", "-q")
    return _commit(path, "start")


def _draft(*candidates: str) -> None:
    draft: dict[str, object] = {
        "version": 1,
        "gate_cases": {"path": BASELINE},
        "kpis": [{"name": "respond_runs", "tags": ["flow"]}],
        "delta_1": 0.0,
        "prior_rho": 0.6,
        "candidates": list(candidates),
        "stop_rules": {"max_cases": 800},
    }
    Path(ROUND).write_text(yaml.safe_dump(draft), encoding="utf-8")


def _locked_base(path: Path, monkeypatch: pytest.MonkeyPatch, graph: str | None = None) -> str:
    head = _repo(path, monkeypatch, graph)
    return head


def _lock(*candidates: str) -> None:
    _draft(*candidates)
    assert lgcov_main(["round", "new"]) == 0


def test_lgcov_guard_still_exits_0_or_2_never_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    head = _locked_base(tmp_path, monkeypatch)
    _lock(head)
    capsys.readouterr()
    assert lgcov_main(["guard"]) == 0
    assert lgcov_main(["guard", "--candidate", OTHER_SHA]) == 2
    with pytest.raises(SystemExit) as exc:
        lgcov_main(["guard", "--candidate", "abc1234"])
    assert exc.value.code == 2


def test_schema_file_is_present() -> None:
    assert SCHEMA.is_file()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert "kpis" in schema.get("properties", {})
    assert "reasons" in schema.get("properties", {})
    assert "exposure_recorded" in schema.get("properties", {})


def test_demo_prompt_tweak_is_blocked_for_root_drift_on_respond(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "proj"
    _locked_base(repo, monkeypatch)
    tweaked = Path("graph.py").read_text(encoding="utf-8").replace(CONCISE, FRIENDLY)
    Path("graph.py").write_text(tweaked, encoding="utf-8")
    candidate = _commit(repo, "prompt tweak")
    _git(repo, "reset", "--hard", "HEAD~1")
    _lock(candidate)
    capsys.readouterr()
    code = lgcov_main(["propose", "--ref", candidate])
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert code == 1
    assert doc["state"] == "blocked"
    assert doc["exposure_recorded"] is False
    assert any("root drift" in r and "respond" in r for r in doc["reasons"])
    assert not any("KPI BLOCK" in r for r in doc["reasons"])
    nodes = {n["id"]: n["label"] for n in doc["nodes"]}
    assert nodes.get("respond") == "root"
    assert "lgcov guard --record" in captured.err


def test_overlay_matches_ref_node_drift_but_is_never_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--node --prompt` vs `--ref` on an equivalent commit: same per-node drift.

    Overlay still cannot be eligible (reason candidate not locked) even if --ref would be
    eligible/blocked — drift labels should match; state may differ because of the lock cap.
    """
    declared = DEMO_GRAPH.read_text(encoding="utf-8") + (
        "\nrespond.system_prompt = SYSTEM_PROMPT\n"
    )
    repo = tmp_path / "proj"
    _locked_base(repo, monkeypatch, declared)
    Path("graph.py").write_text(
        Path("graph.py").read_text(encoding="utf-8").replace(CONCISE, FRIENDLY),
        encoding="utf-8",
    )
    candidate = _commit(repo, "declared prompt tweak")
    _git(repo, "reset", "--hard", "HEAD~1")
    _lock(candidate)
    prompt = tmp_path / "prompt-tweak.txt"
    prompt.write_text(FRIENDLY + "\n", encoding="utf-8")
    capsys.readouterr()
    ref_code = lgcov_main(["propose", "--ref", candidate])
    ref_out = capsys.readouterr()
    ref = json.loads(ref_out.out)
    overlay_code = lgcov_main(["propose", "--node", "respond", "--prompt", str(prompt)])
    overlay_out = capsys.readouterr()
    overlay = json.loads(overlay_out.out)
    ref_nodes = {n["id"]: n["label"] for n in ref["nodes"]}
    overlay_nodes = {n["id"]: n["label"] for n in overlay["nodes"]}
    assert ref_nodes == overlay_nodes
    assert ref_nodes.get("respond") == "root"
    assert ref["state"] == "blocked"
    assert overlay["state"] == "needs_human"
    assert overlay["state"] != "eligible"
    assert any("candidate not locked" in r for r in overlay["reasons"])
    assert ref_code == 1
    assert overlay_code == 0


def test_overlay_without_declared_prompt_is_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "proj"
    head = _locked_base(repo, monkeypatch)
    _lock(head)
    prompt = tmp_path / "guess.txt"
    prompt.write_text(FRIENDLY + "\n", encoding="utf-8")
    capsys.readouterr()
    code = lgcov_main(["propose", "--node", "respond", "--prompt", str(prompt)])
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert doc["state"] == "unsupported"
    assert any("no declared prompt" in r for r in doc["reasons"])
    assert FRIENDLY not in Path("graph.py").read_text(encoding="utf-8")
    assert code == 0


def test_inconclusive_ref_is_needs_human_not_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "proj"
    head = _locked_base(repo, monkeypatch)
    _lock(head)
    capsys.readouterr()
    code = lgcov_main(["propose", "--ref", head])
    doc = json.loads(capsys.readouterr().out)
    assert doc["state"] == "needs_human"
    assert doc["state"] != "eligible"
    assert any("inconclusive" in r.lower() for r in doc["reasons"])
    assert code == 0


def test_fail_on_inconclusive_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "proj"
    head = _locked_base(repo, monkeypatch)
    _lock(head)
    capsys.readouterr()
    code = lgcov_main(["propose", "--ref", head, "--fail-on-inconclusive"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["state"] == "needs_human"
    assert code == 1


def test_optimiser_proposal_k_gate_one_and_full_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "proj"
    _locked_base(repo, monkeypatch)
    bands = {"judges": {"answer_states_order_status": {"band": "blocking"}}}
    Path("calibration").mkdir()
    Path("calibration/bands.json").write_text(json.dumps(bands), encoding="utf-8")
    head = _commit(repo, "bands")
    _lock(head)
    capsys.readouterr()
    lgcov_main(["propose", "--ref", head])
    default = json.loads(capsys.readouterr().out)
    lgcov_main(["propose", "--ref", head, "--optimiser-proposal"])
    opt = json.loads(capsys.readouterr().out)
    names = {k["name"] for k in opt["kpis"]}
    assert "respond_runs" in names
    assert "answer_states_order_status" in names
    assert opt["k_gate"] == 1
    assert default["k_gate"] == 2


def test_optimiser_pool_case_is_not_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "proj"
    head = _locked_base(repo, monkeypatch)
    (repo / "cases/optimiser").mkdir(parents=True)
    (repo / "cases/optimiser" / "opt-only.json").write_text(
        json.dumps({"id": "opt-only", "text": "secret optimiser case that must not be judged"}),
        encoding="utf-8",
    )
    _lock(head)
    capsys.readouterr()
    lgcov_main(["propose", "--ref", head])
    captured = capsys.readouterr()
    assert "opt-only" not in captured.out
    assert "secret optimiser case" not in captured.out


def test_unlocked_ref_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "proj"
    head = _locked_base(repo, monkeypatch)
    _lock(head)
    capsys.readouterr()
    code = lgcov_main(["propose", "--ref", OTHER_SHA])
    err = capsys.readouterr().err
    assert code == 2
    assert "candidate" in err and "not locked" in err


def test_xor_ref_and_overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "proj"
    head = _locked_base(repo, monkeypatch)
    _lock(head)
    prompt = tmp_path / "p.txt"
    prompt.write_text(FRIENDLY, encoding="utf-8")
    with pytest.raises(SystemExit):
        lgcov_main(["propose", "--ref", head, "--node", "respond", "--prompt", str(prompt)])
