"""T127: gate_judge.family vs optimised_model.family — warn only, never fail."""

from __future__ import annotations

from pathlib import Path

import pytest

from lgcov.config import parse_config
from lgcov.judge_family import family_mismatch_warning


def test_no_warning_when_families_match_or_unset() -> None:
    assert family_mismatch_warning(None, None) is None
    assert family_mismatch_warning("openai", "openai") is None
    assert family_mismatch_warning("openai", None) is None
    assert family_mismatch_warning(None, "openai") is None


def test_warning_when_families_differ() -> None:
    note = family_mismatch_warning("openai", "anthropic")
    assert note is not None
    assert "gate_judge.family" in note and "optimised_model.family" in note
    assert "openai" in note and "anthropic" in note
    assert note.startswith("⚠️")


def test_config_loads_gate_judge_and_optimised_model_families() -> None:
    cfg = parse_config(
        {
            "graph_entrypoint": "a.py:g",
            "gate_judge": {"family": "openai"},
            "optimised_model": {"family": "anthropic"},
        }
    )
    assert cfg.gate_judge_family == "openai"
    assert cfg.optimised_model_family == "anthropic"
    assert family_mismatch_warning(cfg.gate_judge_family, cfg.optimised_model_family) is not None


def test_config_rejects_non_mapping_family_block() -> None:
    with pytest.raises(ValueError, match="gate_judge"):
        parse_config({"graph_entrypoint": "a.py:g", "gate_judge": "openai"})


def test_cli_ci_family_mismatch_warns_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_cli import DEMO_GRAPH, _ci_project, _run_ci

    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    (tmp_path / "lgcov.yaml").write_text(
        "graph_entrypoint: graph.py:graph\n"
        "cassette_dir: cassettes\n"
        "gate_judge:\n  family: openai\n"
        "optimised_model:\n  family: anthropic\n",
        encoding="utf-8",
    )
    code, report, _ = _run_ci(tmp_path, "--config", "lgcov.yaml")
    assert code == 0
    assert "gate_judge.family" in report and "optimised_model.family" in report
