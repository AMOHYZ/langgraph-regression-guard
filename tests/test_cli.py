import json
from pathlib import Path

import pytest

from lgcov.cli import main


def test_cli_prints_added_and_removed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    old = {"nodes": ["plan", "answer", "gone"], "edges": [["plan", "answer"]]}
    new = {"nodes": ["plan", "answer", "fmt"], "edges": [["plan", "answer"], ["answer", "fmt"]]}
    (tmp_path / "old.json").write_text(json.dumps(old))
    (tmp_path / "new.json").write_text(json.dumps(new))
    code = main([str(tmp_path / "old.json"), str(tmp_path / "new.json")])
    out = capsys.readouterr().out
    assert code == 1  # changes found
    assert "| + | node | fmt |" in out
    assert "| + | edge | answer → fmt |" in out
    assert "| - | node | gone |" in out


def test_cli_no_changes_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    snap = {"nodes": ["plan"], "edges": []}
    (tmp_path / "old.json").write_text(json.dumps(snap))
    (tmp_path / "new.json").write_text(json.dumps(snap))
    code = main([str(tmp_path / "old.json"), str(tmp_path / "new.json")])
    assert code == 0
    assert "No graph changes." in capsys.readouterr().out


def test_cli_missing_file_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["missing-old.json", "missing-new.json"])
    captured = capsys.readouterr()
    assert code == 2
    assert "graph-diff: file not found: missing-old.json" in captured.err


# Snapshot-format support: golden files (version + edge objects) and legacy
# files (edge pairs) must both load, including mixed old/new comparisons.
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "linear.golden.json"


def test_cli_reads_new_snapshot_format() -> None:
    assert main([str(GOLDEN), str(GOLDEN)]) == 0


def test_cli_reads_legacy_format(tmp_path: Path) -> None:
    p = tmp_path / "old.json"
    p.write_text('{"nodes": ["a", "b"], "edges": [["a", "b"]]}')
    assert main([str(p), str(p)]) == 0


def test_cli_mixed_formats_detect_change(tmp_path: Path) -> None:
    p = tmp_path / "legacy.json"
    p.write_text('{"nodes": ["a", "b"], "edges": [["a", "b"]]}')
    assert main([str(p), str(GOLDEN)]) == 1


def test_cli_bad_input_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = tmp_path / "bad.json"
    p.write_text('{"version": 99, "nodes": [], "edges": []}')
    assert main([str(p), str(p)]) == 2
    assert "cannot read snapshot" in capsys.readouterr().err


# T075: `lgcov taxonomy check <path>` — KPI table on success, named error on invalid.
TAXONOMY = Path(__file__).resolve().parents[1] / "examples" / "open_deep_research" / "taxonomy.yaml"


def test_cli_taxonomy_check_prints_kpi_table(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["taxonomy", "check", str(TAXONOMY)])
    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert "| name | node | judge | labels |" in lines
    assert "| delegates_research | supervisor | jev | pass, fail |" in lines
    assert "| report_grounded | final | jev | pass, fail |" in lines


def test_cli_taxonomy_check_invalid_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    p = tmp_path / "taxonomy.yaml"
    p.write_text(
        "version: 1\nsystem: sandbox\nfailure_modes:\n"
        "  - id: mode_a\n    description: first\n"
        "kpis:\n"
        "  - name: kpi_a\n    failure_mode: ghost\n    node: final\n    judge: fake\n"
        "    description: one line\n    rubric: Is it grounded?\n",
        encoding="utf-8",
    )
    code = main(["taxonomy", "check", str(p)])
    err = capsys.readouterr().err
    assert code == 2
    assert "kpi_a" in err
    assert "Traceback" not in err


# `lgcov replay <graph.py:attr> <baseline.json>`: drifted nodes vs the recorded cassettes.
DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_agent"
DEMO_BASELINE = str(DEMO / "cassettes" / "baseline.json")


def test_cli_replay_unchanged_demo_has_no_drift(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["replay", f"{DEMO / 'graph.py'}:graph", DEMO_BASELINE])
    assert code == 0
    assert "No cassette drift (2 runs)." in capsys.readouterr().out


def test_cli_replay_prompt_tweak_is_root_drift_in_respond(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = (DEMO / "graph.py").read_text(encoding="utf-8")
    tweaked = src.replace("a concise support", "a friendly, detailed support")
    assert tweaked != src
    (tmp_path / "graph.py").write_text(tweaked, encoding="utf-8")
    code = main(["replay", f"{tmp_path / 'graph.py'}:graph", DEMO_BASELINE])
    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert "| `respond` | root | 2/2 |" in lines
    assert "Unchanged: `classify`, `lookup_order`" in lines


@pytest.mark.parametrize(
    "target", ["missing.py:graph", "GRAPH:SYSTEM_PROMPT", "GRAPH:nope", "GRAPH"]
)
def test_cli_replay_bad_target_exits_two(target: str, capsys: pytest.CaptureFixture[str]) -> None:
    target = target.replace("GRAPH", str(DEMO / "graph.py"))
    assert main(["replay", target, DEMO_BASELINE]) == 2
    err = capsys.readouterr().err
    assert "lgcov replay:" in err
    assert "Traceback" not in err


def test_cli_replay_bad_baseline_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    p = tmp_path / "baseline.json"
    p.write_text('{"runs": [{"inputs": {}}]}', encoding="utf-8")
    assert main(["replay", f"{DEMO / 'graph.py'}:graph", str(p)]) == 2
    assert "cannot read baseline" in capsys.readouterr().err
