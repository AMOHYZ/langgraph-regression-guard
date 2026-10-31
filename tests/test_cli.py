import json
import os
import sys
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
# Frozen copies of the demo agent, so editing the demo (as its PR does) can't break these.
FIXTURES = Path(__file__).parent / "fixtures"
DEMO_GRAPH = FIXTURES / "demo_graph.py"
DEMO_BASELINE = str(FIXTURES / "demo_baseline.json")


def test_cli_replay_unchanged_demo_has_no_drift(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["replay", f"{DEMO_GRAPH}:graph", DEMO_BASELINE])
    assert code == 0
    assert "No cassette drift (2 runs)." in capsys.readouterr().out


def test_cli_replay_prompt_tweak_is_root_drift_in_respond(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = DEMO_GRAPH.read_text(encoding="utf-8")
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
    target = target.replace("GRAPH", str(DEMO_GRAPH))
    assert main(["replay", target, DEMO_BASELINE]) == 2
    err = capsys.readouterr().err
    assert "lgcov replay:" in err
    assert "Traceback" not in err


def test_cli_replay_bad_baseline_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    p = tmp_path / "baseline.json"
    p.write_text('{"runs": [{"inputs": {}}]}', encoding="utf-8")
    assert main(["replay", f"{DEMO_GRAPH}:graph", str(p)]) == 2
    assert "cannot read baseline" in capsys.readouterr().err


# T080: replay reads graph_entrypoint / cassette_dir from lgcov.yaml; positionals override.
def test_cli_replay_reads_lgcov_yaml(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "cassettes").mkdir()
    (tmp_path / "cassettes" / "baseline.json").write_text(
        Path(DEMO_BASELINE).read_text(encoding="utf-8"), encoding="utf-8"
    )
    cfg = tmp_path / "lgcov.yaml"
    cfg.write_text(
        f"graph_entrypoint: {DEMO_GRAPH}:graph\ncassette_dir: {tmp_path / 'cassettes'}\n",
        encoding="utf-8",
    )
    assert main(["replay", "--config", str(cfg)]) == 0
    assert "No cassette drift (2 runs)." in capsys.readouterr().out


def test_cli_replay_without_entrypoint_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["replay", "--config", str(tmp_path / "missing.yaml")]) == 2
    assert "graph_entrypoint" in capsys.readouterr().err


# T077 / T084: `lgcov --version` reports the installed package version.
def test_cli_version_matches_package_metadata(capsys: pytest.CaptureFixture[str]) -> None:
    from importlib.metadata import version

    assert main(["--version"]) == 0
    assert capsys.readouterr().out == f"lgcov {version('langgraph-regression-guard')}\n"


# T077: `lgcov record --inputs inputs.json` writes the baseline that `lgcov replay` reads.
def _demo_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "lgcov.yaml"
    cfg.write_text(
        f"graph_entrypoint: {DEMO_GRAPH}:graph\ncassette_dir: {tmp_path / 'cassettes'}\n",
        encoding="utf-8",
    )
    return cfg


def test_cli_record_then_replay_round_trips(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _demo_config(tmp_path)
    inputs = tmp_path / "inputs.json"
    inputs.write_text('[{"question": "Where is my order?"}, {"question": "What are your hours?"}]')
    assert main(["record", "--config", str(cfg), "--inputs", str(inputs)]) == 0
    written = (tmp_path / "cassettes" / "baseline.json").read_text(encoding="utf-8")
    assert written == Path(DEMO_BASELINE).read_text(encoding="utf-8")
    assert "recorded 2 runs" in capsys.readouterr().out
    assert main(["replay", "--config", str(cfg)]) == 0


@pytest.mark.parametrize("body", ['{"question": "x"}', "[1, 2]", "not json"])
def test_cli_record_bad_inputs_exits_two(
    tmp_path: Path, body: str, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = tmp_path / "inputs.json"
    inputs.write_text(body)
    code = main(["record", "--config", str(_demo_config(tmp_path)), "--inputs", str(inputs)])
    assert code == 2
    assert "lgcov record:" in capsys.readouterr().err
    assert not (tmp_path / "cassettes" / "baseline.json").exists()


# T082: `lgcov cassette accept --nodes ...` re-baselines only the named nodes.
AB_GRAPH = """\
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class S(TypedDict, total=False):
    a: str
    b: str


def a(state: S) -> S:
    return {"a": "A1"}


def b(state: S) -> S:
    return {"b": "B1"}


g = StateGraph(S)
g.add_node("a", a)
g.add_node("b", b)
g.add_edge(START, "a")
g.add_edge("a", "b")
g.add_edge("b", END)
graph = g.compile()
"""


def _ab_project(tmp_path: Path) -> tuple[Path, Path]:
    """Record a baseline for AB_GRAPH, then change both nodes; returns (config, baseline)."""
    graph = tmp_path / "graph.py"
    graph.write_text(AB_GRAPH, encoding="utf-8")
    cfg = tmp_path / "lgcov.yaml"
    cfg.write_text(
        f"graph_entrypoint: {graph}:graph\ncassette_dir: {tmp_path / 'cassettes'}\n",
        encoding="utf-8",
    )
    inputs = tmp_path / "inputs.json"
    inputs.write_text("[{}, {}]", encoding="utf-8")
    assert main(["record", "--config", str(cfg), "--inputs", str(inputs)]) == 0
    graph.write_text(AB_GRAPH.replace("A1", "A2").replace("B1", "B2"), encoding="utf-8")
    return cfg, tmp_path / "cassettes" / "baseline.json"


def test_cli_accept_b_leaves_a_byte_identical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, baseline = _ab_project(tmp_path)
    before = baseline.read_text(encoding="utf-8")
    capsys.readouterr()
    assert main(["cassette", "accept", "--nodes", "b", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "| `b` |" in out
    assert str(baseline) in out
    after = baseline.read_text(encoding="utf-8")
    runs = json.loads(before)["runs"]
    for run, new in zip(runs, json.loads(after)["runs"], strict=True):
        assert new["steps"][1]["output"] == {"b": "B2"}
        run["steps"][1] = new["steps"][1]  # b's steps are re-recorded whole (output, input, code)
    # ...and every other byte, a's entries included, is untouched.
    assert after == json.dumps({"runs": runs}, indent=2, sort_keys=True) + "\n"
    summary = baseline.parent / "accept.md"
    assert summary.is_file()
    assert "| `b` |" in summary.read_text(encoding="utf-8")
    assert main(["replay", "--config", str(cfg)]) == 1
    assert "| `a` | root | 2/2 |" in capsys.readouterr().out.splitlines()


def test_cli_accept_node_without_drift_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, baseline = _ab_project(tmp_path)
    (tmp_path / "graph.py").write_text(AB_GRAPH.replace("B1", "B2"), encoding="utf-8")
    before = baseline.read_text(encoding="utf-8")
    capsys.readouterr()
    assert main(["cassette", "accept", "--nodes", "a", "--config", str(cfg)]) == 1
    assert "not drifted" in capsys.readouterr().err
    assert baseline.read_text(encoding="utf-8") == before


def test_cli_accept_unknown_node_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, baseline = _ab_project(tmp_path)
    before = baseline.read_text(encoding="utf-8")
    capsys.readouterr()
    assert main(["cassette", "accept", "--nodes", "b,ghost", "--config", str(cfg)]) == 2
    assert "unknown node 'ghost'" in capsys.readouterr().err
    assert baseline.read_text(encoding="utf-8") == before  # nothing half-applied


def test_cli_accept_all_drifted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg, baseline = _ab_project(tmp_path)
    before = baseline.read_text(encoding="utf-8")
    capsys.readouterr()
    assert main(["cassette", "accept", "--all-drifted", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "| `a` |" in out and "| `b` |" in out
    after = json.loads(baseline.read_text(encoding="utf-8"))
    want = json.loads(before.replace('"A1"', '"A2"').replace('"B1"', '"B2"'))
    for step in (s for r in (*after["runs"], *want["runs"]) for s in r["steps"]):
        del step["code"]  # fingerprints of the edited code, so they change too
    assert after == want
    assert main(["replay", "--config", str(cfg)]) == 0


def test_cli_accept_cassette_dir_flag_overrides_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, _ = _ab_project(tmp_path)
    other = tmp_path / "other_cassettes"
    other.mkdir()
    (tmp_path / "cassettes" / "baseline.json").replace(other / "baseline.json")
    capsys.readouterr()
    assert (
        main(
            [
                "cassette",
                "accept",
                "--nodes",
                "b",
                "--config",
                str(cfg),
                "--cassette-dir",
                str(other),
            ]
        )
        == 0
    )
    assert (other / "baseline.json").read_text(encoding="utf-8").count('"B2"') == 2
    assert (other / "accept.md").is_file()


def test_cli_replay_sees_edit_with_same_size_and_mtime(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: _load_graph reused a stale __pycache__ .pyc when an edit kept the
    # file's size and (second-resolution) mtime, so replay ran the old graph.
    # Bytecode writing is forced on: CI writes .pyc files even where dev shells don't.
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    cfg, _ = _ab_project(tmp_path)  # records with A1/B1, then rewrites to A2/B2
    graph = tmp_path / "graph.py"
    graph.write_text(AB_GRAPH, encoding="utf-8")
    stat = graph.stat()
    assert main(["replay", "--config", str(cfg)]) == 0  # caches bytecode for A1/B1
    graph.write_text(AB_GRAPH.replace("A1", "A2"), encoding="utf-8")  # same size
    os.utime(graph, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # same mtime
    capsys.readouterr()
    assert main(["replay", "--config", str(cfg)]) == 1
    assert "| `a` | root | 2/2 |" in capsys.readouterr().out.splitlines()


# T085: ignore_paths in lgcov.yaml reach `lgcov replay`.
def test_cli_replay_applies_ignore_paths_from_config(tmp_path: Path) -> None:
    graph = tmp_path / "graph.py"
    graph.write_text(
        AB_GRAPH.replace("import TypedDict", "import TypedDict\nimport random")
        .replace("    b: str\n", "    b: str\n    nonce: float\n")
        .replace('return {"b": "B1"}', 'return {"b": "B1", "nonce": random.random()}'),
        encoding="utf-8",
    )
    cfg = tmp_path / "lgcov.yaml"
    base = f"graph_entrypoint: {graph}:graph\ncassette_dir: {tmp_path / 'cassettes'}\n"
    cfg.write_text(base, encoding="utf-8")
    (tmp_path / "inputs.json").write_text("[{}]", encoding="utf-8")
    assert main(["record", "--config", str(cfg), "--inputs", str(tmp_path / "inputs.json")]) == 0
    # --live: unchanged b would be served from the cassette, never rolling a new nonce.
    assert main(["replay", "--live", "--config", str(cfg)]) == 1  # the random nonce drifts
    cfg.write_text(base + "ignore_paths: [/nonce]\n", encoding="utf-8")
    assert main(["replay", "--live", "--config", str(cfg)]) == 0


# Replay serves a node from the cassette when its code fingerprint and input match.
def test_cli_replay_serves_unchanged_nodes_unless_live(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, baseline = _ab_project(tmp_path)
    (tmp_path / "graph.py").write_text(AB_GRAPH, encoding="utf-8")  # the recorded code again
    # A hand-edited output is served as recorded, which shows `a` did not run...
    edited = baseline.read_text(encoding="utf-8").replace('"A1"', '"AX"')
    baseline.write_text(edited, encoding="utf-8")
    assert main(["replay", "--config", str(cfg)]) == 0
    # ...and --live re-runs every node, which catches the edit.
    capsys.readouterr()
    assert main(["replay", "--live", "--config", str(cfg)]) == 1
    assert "| `a` | root | 2/2 |" in capsys.readouterr().out.splitlines()


def test_cli_replay_still_reports_root_and_downstream_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # b's code is unchanged but it reads a's output, so it re-runs once a changes.
    src = AB_GRAPH.replace('return {"b": "B1"}', 'return {"b": state["a"] + "!"}')
    graph = tmp_path / "graph.py"
    graph.write_text(src, encoding="utf-8")
    cfg = tmp_path / "lgcov.yaml"
    cfg.write_text(
        f"graph_entrypoint: {graph}:graph\ncassette_dir: {tmp_path / 'cassettes'}\n",
        encoding="utf-8",
    )
    (tmp_path / "inputs.json").write_text("[{}]", encoding="utf-8")
    assert main(["record", "--config", str(cfg), "--inputs", str(tmp_path / "inputs.json")]) == 0
    graph.write_text(src.replace("A1", "A2"), encoding="utf-8")
    capsys.readouterr()
    assert main(["replay", "--config", str(cfg)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert "| `a` | root | 1/1 |" in lines
    assert "| `b` | downstream | 1/1 |" in lines


def test_cli_replay_reads_a_baseline_recorded_before_node_inputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Older baselines have no node inputs or code fingerprints: every node runs live.
    runs = json.loads(Path(DEMO_BASELINE).read_text(encoding="utf-8"))["runs"]
    legacy = [
        {
            "inputs": r["inputs"],
            "steps": [{"node": s["node"], "output": s["output"]} for s in r["steps"]],
        }
        for r in runs
    ]
    (tmp_path / "baseline.json").write_text(json.dumps({"runs": legacy}), encoding="utf-8")
    src = DEMO_GRAPH.read_text(encoding="utf-8")
    (tmp_path / "graph.py").write_text(
        src.replace("a concise support", "a friendly, detailed support"), encoding="utf-8"
    )
    assert main(["replay", f"{tmp_path / 'graph.py'}:graph", str(tmp_path / "baseline.json")]) == 1
    assert "| `respond` | root | 2/2 |" in capsys.readouterr().out.splitlines()
