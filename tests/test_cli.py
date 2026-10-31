import json
import os
import sys
from pathlib import Path

import pytest

from lgcov.cli import main
from lgcov.judges import KPI, NodeState, Verdict


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


# T078 PR A: `lgcov snapshot`, `replay --fail-on/--summary-json`, harness errors exit 2.
def test_cli_snapshot_prints_a_graph_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from lgcov.snapshot import GraphSnapshot

    assert main(["snapshot", "--graph", f"{DEMO_GRAPH}:graph"]) == 0
    out = capsys.readouterr().out
    snap = GraphSnapshot.from_json(out)
    assert {"classify", "lookup_order", "respond"} <= snap.nodes
    conditional = {(e.source, e.target) for e in snap.edges if e.conditional}
    assert conditional == {("classify", "lookup_order"), ("classify", "respond")}
    path = tmp_path / "snap.json"
    path.write_text(out, encoding="utf-8")
    assert main([str(path), str(path)]) == 0
    assert "No graph changes." in capsys.readouterr().out


def test_cli_snapshot_bad_target_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["snapshot", "--graph", f"{tmp_path / 'missing.py'}:graph"]) == 2
    assert "lgcov snapshot:" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["replay", "snapshot"])
def test_cli_unloadable_entrypoint_is_a_harness_error(
    tmp_path: Path, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = tmp_path / "graph.py"
    graph.write_text('raise RuntimeError("boom at import")\n', encoding="utf-8")
    target = f"{graph}:graph"
    args = [command, target, DEMO_BASELINE] if command == "replay" else [command, "--graph", target]
    assert main(args) == 2
    err = capsys.readouterr().err
    assert "harness error, not a regression" in err
    assert "boom at import" in err
    assert "Traceback" not in err


def test_cli_replay_node_crash_is_an_agent_failure_even_with_fail_on_never(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, _ = _ab_project(tmp_path)
    crashing = AB_GRAPH.replace('return {"b": "B1"}', 'raise RuntimeError("node b crashed")')
    (tmp_path / "graph.py").write_text(crashing, encoding="utf-8")
    capsys.readouterr()
    assert main(["replay", "--config", str(cfg)]) == 1
    assert "agent failure: node `b` raised RuntimeError: node b crashed" in capsys.readouterr().out
    # fail-on governs drift only: a graph that throws is never swallowed.
    assert main(["replay", "--config", str(cfg), "--fail-on", "never"]) == 1


def test_cli_replay_network_error_in_a_node_is_a_harness_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, _ = _ab_project(tmp_path)
    slow = AB_GRAPH.replace('return {"b": "B1"}', 'raise TimeoutError("provider timed out")')
    (tmp_path / "graph.py").write_text(slow, encoding="utf-8")
    capsys.readouterr()
    assert main(["replay", "--config", str(cfg)]) == 2
    assert "harness error (transport) in node `b`, not a regression" in capsys.readouterr().err


def test_cli_replay_missing_cassette_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, baseline = _ab_project(tmp_path)
    baseline.unlink()
    capsys.readouterr()
    assert main(["replay", "--config", str(cfg)]) == 2
    assert "harness error, not a regression" in capsys.readouterr().err


def test_cli_replay_summary_json_counts_root_and_propagated(tmp_path: Path) -> None:
    cfg, _ = _ab_project(tmp_path)  # a and b both changed: a drifts first (root), b after
    summary = tmp_path / "summary.json"
    assert main(["replay", "--config", str(cfg), "--summary-json", str(summary)]) == 1
    assert json.loads(summary.read_text(encoding="utf-8")) == {
        "exit_code": 1,
        "fail_on": "any-drift",
        "propagated_drift": ["b"],
        "propagated_drift_count": 1,
        "root_drift": ["a"],
        "root_drift_count": 1,
        "runs": 2,
        "verdict": "fail",
    }


def test_cli_replay_prompt_tweak_summary_is_root_only(tmp_path: Path) -> None:
    graph = tmp_path / "graph.py"
    tweaked = DEMO_GRAPH.read_text(encoding="utf-8").replace("a concise", "a friendly")
    graph.write_text(tweaked, encoding="utf-8")
    summary = tmp_path / "summary.json"
    args = ["replay", f"{graph}:graph", DEMO_BASELINE, "--summary-json", str(summary)]
    assert main([*args, "--fail-on", "root-drift"]) == 1
    data = json.loads(summary.read_text(encoding="utf-8"))
    assert (data["root_drift"], data["propagated_drift"]) == (["respond"], [])


def test_cli_replay_fail_on_never_exits_zero_on_drift(tmp_path: Path) -> None:
    cfg, _ = _ab_project(tmp_path)
    assert main(["replay", "--config", str(cfg), "--fail-on", "never"]) == 0


def test_cli_replay_fail_on_typo_lists_allowed_values(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["replay", "--fail-on", "root-drif"])
    assert exc.value.code == 2
    assert "root-drift, any-drift, never; got 'root-drif'" in capsys.readouterr().err


# T078 PR B: `lgcov ci` builds the Action's report, step outputs and exit code.
def _ci_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, graph_src: str) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "graph.py").write_text(graph_src, encoding="utf-8")
    (tmp_path / "cassettes").mkdir()
    (tmp_path / "cassettes" / "baseline.json").write_text(
        Path(DEMO_BASELINE).read_text(encoding="utf-8"), encoding="utf-8"
    )


def _run_ci(tmp_path: Path, *extra: str) -> tuple[int, str, dict[str, str]]:
    report, outputs = tmp_path / "out" / "report.md", tmp_path / "out" / "outputs.txt"
    report.parent.mkdir(exist_ok=True)
    args = ["ci", "--report", str(report), "--github-output", str(outputs)]
    code = main([*args, "--graph", "graph.py:graph", "--cassette-dir", "cassettes", *extra])
    lines = outputs.read_text(encoding="utf-8").splitlines()
    return (
        code,
        report.read_text(encoding="utf-8"),
        {k: v for k, v in (x.split("=", 1) for x in lines)},
    )


def test_cli_ci_reports_root_drift_and_sets_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    _ci_project(tmp_path, monkeypatch, demo.replace("a concise", "a friendly"))
    (tmp_path / "base").mkdir()
    (tmp_path / "base" / "graph.py").write_text(demo, encoding="utf-8")
    code, report, outputs = _run_ci(tmp_path, "--base-dir", "base")
    assert code == 1
    assert report.startswith("<!-- lgcov-report -->\n## 🔴 ")
    assert "No graph changes." in report
    assert "| `respond` | root | 2/2 |" in report
    assert "fail-on: `root-drift`" in report
    assert outputs == {
        "exit-code": "1",
        "propagated-drift-count": "0",
        "report-path": str(tmp_path / "out" / "report.md"),
        "root-drift-count": "1",
        "tier": "pr",  # T089: the effective tier, for the Action's nightly upload
        "verdict": "fail",
    }


def test_cli_ci_missing_base_graph_lists_every_node_as_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    (tmp_path / "base").mkdir()
    code, report, outputs = _run_ci(tmp_path, "--base-dir", "base")
    assert (code, outputs["verdict"]) == (0, "pass")
    assert report.startswith("<!-- lgcov-report -->\n## 🟢 ")
    assert "every node is new" in report
    assert "| + | node | respond |" in report


def test_cli_ci_unloadable_graph_is_a_harness_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, 'raise RuntimeError("boom at import")\n')
    code, report, outputs = _run_ci(tmp_path)
    assert code == 2
    assert "⚠️ Harness error, not a regression" in report
    assert "boom at import" in report
    assert "Coverage:" not in report
    assert (outputs["verdict"], outputs["exit-code"], outputs["root-drift-count"]) == (
        "error",
        "2",
        "0",
    )


def test_cli_ci_reports_an_earlier_install_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    code, report, outputs = _run_ci(tmp_path, "--harness-error", "uv sync --locked failed")
    assert (code, outputs["verdict"]) == (2, "error")
    assert "⚠️ Harness error, not a regression" in report
    assert "uv sync --locked failed" in report


def test_cli_ci_fail_on_never_passes_but_still_shows_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    _ci_project(tmp_path, monkeypatch, demo.replace("a concise", "a friendly"))
    code, report, outputs = _run_ci(tmp_path, "--fail-on", "never")
    assert (code, outputs["verdict"], outputs["root-drift-count"]) == (0, "pass", "1")
    assert "| `respond` | root | 2/2 |" in report


def test_cli_ci_serves_unchanged_nodes_unless_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    baseline = tmp_path / "cassettes" / "baseline.json"
    # A hand-edited tool result is served as recorded, which shows the nodes did not run...
    edited = baseline.read_text(encoding="utf-8").replace("#42: shipped", "#42: lost")
    baseline.write_text(edited, encoding="utf-8")
    assert _run_ci(tmp_path)[0] == 0
    # ...and --live re-runs every node, which catches the edit.
    code, report, _ = _run_ci(tmp_path, "--live")
    assert code == 1
    assert "| `lookup_order` | root | 1/2 |" in report


# T083: `lgcov coverage` from the recorded runs (nothing re-runs), with trend vs main.
def _coverage_args(tmp_path: Path, runs: list[int] | None = None) -> list[str]:
    data = json.loads(Path(DEMO_BASELINE).read_text(encoding="utf-8"))
    if runs is not None:
        data["runs"] = [data["runs"][i] for i in runs]
    (tmp_path / "cassettes").mkdir(exist_ok=True)
    (tmp_path / "cassettes" / "baseline.json").write_text(json.dumps(data), encoding="utf-8")
    cassettes = str(tmp_path / "cassettes")
    return ["coverage", "--graph", f"{DEMO_GRAPH}:graph", "--cassette-dir", cassettes]


def test_cli_coverage_out_writes_the_snapshot_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from lgcov.coverage import CoverageSnapshot

    out = tmp_path / "coverage.json"
    args = [*_coverage_args(tmp_path), "--commit", "abc123", "--out", str(out)]
    assert main(args) == 0
    cov = CoverageSnapshot.from_json(out.read_text(encoding="utf-8"))
    assert (cov.nodes_total, cov.nodes_hit, cov.commit_sha) == (5, 5, "abc123")
    assert "Nodes 100% · Edges 100% (no baseline yet)" in capsys.readouterr().out


def test_cli_coverage_baseline_prints_the_trend_and_newly_uncovered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main_cov = tmp_path / "coverage.main.json"
    assert main([*_coverage_args(tmp_path), "--out", str(main_cov)]) == 0
    capsys.readouterr()
    # Only the hours run: lookup_order is never visited.
    assert main([*_coverage_args(tmp_path, runs=[1]), "--baseline", str(main_cov)]) == 0
    out = capsys.readouterr().out
    assert "Nodes 80% (−20% vs main) · Edges 60% (−40% vs main)" in out
    assert "Newly uncovered: `lookup_order`" in out


def test_cli_coverage_missing_baseline_is_no_baseline_yet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = [*_coverage_args(tmp_path), "--baseline", str(tmp_path / "missing.json")]
    assert main(args) == 0
    assert "(no baseline yet)" in capsys.readouterr().out


def test_cli_coverage_malformed_baseline_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "coverage.main.json"
    bad.write_text("{}", encoding="utf-8")
    assert main([*_coverage_args(tmp_path), "--baseline", str(bad)]) == 2
    assert "lgcov coverage:" in capsys.readouterr().err


# T083 C2: `lgcov ci` reports coverage of the runs replay just did, and the trend vs main.
def test_cli_ci_reports_coverage_with_no_baseline_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lgcov.coverage import CoverageSnapshot

    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    cov_out = tmp_path / "out" / "coverage.json"
    code, report, _ = _run_ci(tmp_path, "--coverage-out", str(cov_out), "--commit", "abc123")
    assert code == 0
    assert "Coverage: Nodes 100% · Edges 100% (no baseline yet)" in report
    cov = CoverageSnapshot.from_json(cov_out.read_text(encoding="utf-8"))
    assert (cov.nodes_hit, cov.commit_sha) == (5, "abc123")


def test_cli_ci_coverage_trend_lists_newly_uncovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    never_the_tool = demo.replace('"order" in state["question"].lower()', "False")
    assert never_the_tool != demo
    _ci_project(tmp_path, monkeypatch, never_the_tool)
    main_cov = tmp_path / "coverage.main.json"
    args = ["coverage", "--graph", f"{DEMO_GRAPH}:graph", "--cassette-dir", "cassettes"]
    assert main([*args, "--out", str(main_cov)]) == 0  # main covered every node
    code, report, _ = _run_ci(tmp_path, "--coverage-baseline", str(main_cov))
    assert code == 1  # classify drifted
    assert "Coverage: Nodes 80% (−20% vs main) · Edges 60% (−40% vs main)" in report
    assert "Newly uncovered: `lookup_order`" in report


def test_cli_ci_unreadable_coverage_baseline_is_no_baseline_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    bad = tmp_path / "coverage.main.json"
    bad.write_text("not json", encoding="utf-8")
    code, report, _ = _run_ci(tmp_path, "--coverage-baseline", str(bad))
    assert code == 0  # a bad baseline is a note, never a failure
    assert "(no baseline yet)" in report
    assert "could not read main's coverage.json" in report.lower()


# thresholds.min_node_coverage: under the floor, `lgcov ci` fails the run and names the gap.
def test_cli_ci_fails_when_node_coverage_is_under_the_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    data = json.loads(Path(DEMO_BASELINE).read_text(encoding="utf-8"))
    data["runs"] = data["runs"][1:]  # only the hours run: lookup_order is never visited
    (tmp_path / "cassettes" / "baseline.json").write_text(json.dumps(data), encoding="utf-8")
    config = tmp_path / "lgcov.yaml"
    config.write_text("thresholds: {min_node_coverage: 80}\n", encoding="utf-8")
    code, report, outputs = _run_ci(tmp_path)
    assert (code, outputs["verdict"]) == (0, "pass")  # 80% meets an 80% floor
    config.write_text("thresholds: {min_node_coverage: 90}\n", encoding="utf-8")
    code, report, outputs = _run_ci(tmp_path)
    assert (code, outputs["verdict"], outputs["root-drift-count"]) == (1, "fail", "0")
    assert report.startswith("<!-- lgcov-report -->\n## 🔴 lgcov: coverage under the floor")
    assert (
        "🔴 Node coverage 80% is under the 90% floor (`thresholds.min_node_coverage`); "
        "never run: `lookup_order`"
    ) in report


# T073: `lgcov judge` runs the taxonomy's judges k times per case, base and fresh outputs.
DEMO_TAXONOMY = str(FIXTURES / "demo_taxonomy.yaml")


def _judge_args(tmp_path: Path) -> list[str]:
    (tmp_path / "cassettes").mkdir(exist_ok=True)
    (tmp_path / "cassettes" / "baseline.json").write_text(
        Path(DEMO_BASELINE).read_text(encoding="utf-8"), encoding="utf-8"
    )
    cassettes = str(tmp_path / "cassettes")
    return ["judge", "--graph", f"{DEMO_GRAPH}:graph", "--cassette-dir", cassettes]


def test_cli_judge_samples_three_and_shows_a_flaky_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*_judge_args(tmp_path), "--taxonomy", DEMO_TAXONOMY, "--samples", "3"]) == 0
    out = capsys.readouterr().out
    assert "Planned: 24 judge calls, 12 to model judges (--max-calls 200)" in out
    assert "| respond_runs (n=2, k=3) | 100% | 100% | 0% |" in out
    assert "| answer_states_order_status (n=2, k=3) | 100% | 50% | ⚠️ 50% |" in out
    assert "Top flaky cases, answer_states_order_status: `run-0/respond`" in out
    # T091: the paired gate table; the same flaky case on both sides is no change, and
    # with no spread in the differences there is no MDE to give.
    assert "| KPI | n | base | PR | Δ (95% CI) | p adj. | δ | MDE | N* | verdict | root |" in out
    assert (
        "| answer_states_order_status | 2 | 83% | 83% | +0.00 [+0.00, +0.00] | 1.000 | 0.05 "
        "| — | — | 🟢 SHIP | — |"
    ) in out


# T091: base passes both cases and the PR fails its order case: n=2 can't decide that.
UNDECIDED_TAXONOMY = """\
version: 1
system: demo_agent
failure_modes:
  - id: respond_drift
    description: The answer loses the order status.
kpis:
  - name: answer_states_order_status
    failure_mode: respond_drift
    node: respond
    judge: fake
    description: The 2nd order answer judged fails; with k=1 that is the PR's.
    rubric: Does the answer state the order's status?
    check: {fail_every: "2", when_input_contains: "order"}
"""


def test_cli_judge_inconclusive_exits_zero_unless_gate_inconclusive_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    taxonomy = tmp_path / "taxonomy.yaml"
    taxonomy.write_text(UNDECIDED_TAXONOMY, encoding="utf-8")
    args = [*_judge_args(tmp_path), "--taxonomy", str(taxonomy)]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "| answer_states_order_status | 2 | 100% | 50% |" in out
    assert "⚠️ INCONCLUSIVE" in out
    assert "| ⚠️ underpowered: need 6 more cases for answer_states_order_status (N*=8) |" in out
    config = tmp_path / "lgcov.yaml"
    config.write_text("gate: {inconclusive_blocks: true}\n", encoding="utf-8")
    assert main([*args, "--config", str(config)]) == 1
    assert main([*args, "--fail-on-inconclusive"]) == 1


def test_cli_judge_refuses_more_model_calls_than_max_calls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = [*_judge_args(tmp_path), "--taxonomy", DEMO_TAXONOMY, "--samples", "3"]
    assert main([*args, "--max-calls", "5"]) == 2
    err = capsys.readouterr().err
    assert "12 model judge calls" in err
    assert "--max-calls 5" in err


def test_cli_judge_needs_a_taxonomy(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([*_judge_args(tmp_path), "--config", str(tmp_path / "none.yaml")]) == 2
    assert "taxonomy" in capsys.readouterr().err


# T074: in the Action, a node crash is an agent failure that names the node.
def test_cli_ci_node_crash_is_an_agent_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    crashing = demo.replace(
        'return {"needs_tool": "order" in state["question"].lower()}',
        'raise ValueError("cannot classify")',
    )
    assert crashing != demo
    _ci_project(tmp_path, monkeypatch, crashing)
    code, report, outputs = _run_ci(tmp_path, "--fail-on", "never")
    assert code == 1
    assert report.startswith("<!-- lgcov-report -->\n## 🔴 lgcov: agent failure")
    assert "Node `classify` raised ValueError: cannot classify" in report
    assert (outputs["verdict"], outputs["root-drift-count"]) == ("fail", "1")


def test_cli_ci_network_error_in_a_node_names_the_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    slow = demo.replace(
        'return {"needs_tool": "order" in state["question"].lower()}',
        'raise TimeoutError("provider timed out")',
    )
    _ci_project(tmp_path, monkeypatch, slow)
    code, report, outputs = _run_ci(tmp_path)
    assert (code, outputs["verdict"]) == (2, "error")
    assert "⚠️ Harness error (transport) in node `classify`, not a regression" in report


def test_cli_ci_missing_cassette_is_a_harness_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    (tmp_path / "cassettes" / "baseline.json").unlink()
    code, report, _ = _run_ci(tmp_path)
    assert code == 2
    assert "⚠️ Harness error, not a regression" in report


# T091: with a taxonomy, the Action's run gates the KPIs and the PR comment shows the table.
def test_cli_ci_gates_kpis_and_fails_on_inconclusive_only_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    (tmp_path / "taxonomy.yaml").write_text(UNDECIDED_TAXONOMY, encoding="utf-8")
    code, report, outputs = _run_ci(tmp_path, "--taxonomy", "taxonomy.yaml")
    assert (code, outputs["verdict"]) == (0, "pass")
    assert report.startswith("<!-- lgcov-report -->\n## ⚠️ lgcov: inconclusive, needs a human")
    assert "| answer_states_order_status | 2 | 100% | 50% |" in report
    assert "| ⚠️ underpowered: need 6 more cases for answer_states_order_status" in report
    code, report, outputs = _run_ci(
        tmp_path, "--taxonomy", "taxonomy.yaml", "--fail-on-inconclusive"
    )
    assert (code, outputs["verdict"]) == (1, "fail")
    assert report.startswith("<!-- lgcov-report -->\n## 🔴 lgcov: inconclusive")


def test_cli_ci_refuses_more_model_judge_calls_than_max_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    (tmp_path / "taxonomy.yaml").write_text(UNDECIDED_TAXONOMY, encoding="utf-8")
    code, report, _ = _run_ci(tmp_path, "--taxonomy", "taxonomy.yaml", "--max-calls", "3")
    assert code == 2  # 4 model judge calls planned: a harness limit, not a regression
    assert "refusing to make 4 model judge calls" in report


# T089: classify rewrites the question (the demo's classify has no prompt), so the nodes
# after it get inputs the recording doesn't have: they diverge.
def _rewriting_classify() -> str:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    src = demo.replace(
        'return {"needs_tool": "order" in state["question"].lower()}',
        'return {"question": state["question"].lower(), '
        '"needs_tool": "order" in state["question"].lower()}',
    )
    assert src != demo
    return src


def test_cli_ci_divergence_flag_reruns_live_and_marks_what_ran_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, _rewriting_classify())
    code, report, _ = _run_ci(tmp_path)  # on-divergence: flag, the default
    assert code == 1  # classify is root drift
    assert "Diverged, so run live (on-divergence: flag): `lookup_order` in run 1, " in report
    assert "`respond` in run 2" in report
    assert "Re-executed after divergence: `respond` in run 1." in report
    assert "(5 live / 0 served)" in report


def test_cli_ci_divergence_fail_leaves_the_rest_of_the_run_not_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, _rewriting_classify())
    args = ("--on-divergence", "fail", "--fail-on", "never")
    code, report, outputs = _run_ci(tmp_path, *args)
    assert (code, outputs["verdict"]) == (0, "pass")  # INCONCLUSIVE exits 0 by default
    assert report.startswith("<!-- lgcov-report -->\n## ⚠️ lgcov: inconclusive, needs a human")
    assert (
        "Not checked (on-divergence: fail): run 1 from `lookup_order` (`lookup_order`, "
        "`respond`), run 2 from `respond` (`respond`)."
    ) in report
    assert "Unchanged:" not in report  # never claim a node that wasn't checked
    code, _, _ = _run_ci(tmp_path, *args, "--fail-on-inconclusive")
    assert code == 1  # never 2


def test_cli_ci_divergence_live_runs_the_rest_of_each_run_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, _rewriting_classify())
    _, report, _ = _run_ci(tmp_path, "--on-divergence", "live")
    assert (
        "Diverged, so the rest of the run ran live (on-divergence: live): `lookup_order` in "
        "run 1, `respond` in run 2."
    ) in report


def test_cli_ci_budget_hit_leaves_nodes_not_checked_and_is_never_a_harness_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, _rewriting_classify())
    # 0 s: the first live call runs (nothing measured yet), the next one doesn't start.
    code, report, _ = _run_ci(tmp_path, "--budget-seconds", "0", "--fail-on", "never")
    assert code == 0
    assert "· budget 0 s · budget hit at node `lookup_order` after " in report
    assert "Not checked (budget hit): run 1 from `lookup_order` (`lookup_order`, " in report
    assert "run 2 from `classify` (`classify`, `respond`)." in report
    assert "Harness error" not in report


def test_cli_ci_timing_line_counts_served_calls_and_its_phases_add_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import itertools
    import re

    ticks = itertools.count()  # every clock read is 1 s later
    monkeypatch.setattr("lgcov.cli.perf_counter", lambda: float(next(ticks)))
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    (tmp_path / "taxonomy.yaml").write_text(UNDECIDED_TAXONOMY, encoding="utf-8")
    _, report, _ = _run_ci(tmp_path, "--taxonomy", "taxonomy.yaml", "--budget-seconds", "900")
    line = re.search(
        r"⏱ (\d+) s · agent (\d+) s \(0 live / 5 served\) · judges (\d+) s \(4 calls\) "
        r"· budget 900 s",
        report,
    )
    assert line is not None, report
    total, agent, judges = (int(g) for g in line.groups())
    assert agent > 0 and judges > 0 and agent + judges < total  # the rest is overhead


def test_cli_judge_node_crash_is_an_agent_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = tmp_path / "crashing.py"
    graph.write_text(
        DEMO_GRAPH.read_text(encoding="utf-8").replace(
            'return {"needs_tool": "order" in state["question"].lower()}',
            'raise ValueError("cannot classify")',
        ),
        encoding="utf-8",
    )
    args = [*_judge_args(tmp_path), "--taxonomy", DEMO_TAXONOMY, "--graph", f"{graph}:graph"]
    assert main(args) == 1
    assert "agent failure: node `classify` raised ValueError" in capsys.readouterr().out


# T074 PR B: `lgcov selfcheck` runs the harness on fixtures whose outcome is known.
SELFCHECK = FIXTURES / "selfcheck"


def test_cli_selfcheck_passes_on_its_fixtures(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["selfcheck", "--dir", str(SELFCHECK)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "| broken_harness | INFRA_ERROR | INFRA_ERROR | — | ✓ |" in out
    assert "| broken_node | AGENT_FAIL @ classify | AGENT_FAIL | classify | ✓ |" in out
    assert "| broken_prompt | AGENT_FAIL @ respond | AGENT_FAIL | respond | ✓ |" in out
    assert "| good_change | PASS | PASS | — | ✓ |" in out


def test_cli_selfcheck_exits_one_on_a_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil

    shutil.copytree(SELFCHECK, tmp_path / "selfcheck")
    expected = tmp_path / "selfcheck" / "good_change" / "expected.yaml"
    expected.write_text("outcome: AGENT_FAIL\n", encoding="utf-8")
    assert main(["selfcheck", "--dir", str(tmp_path / "selfcheck")]) == 1
    assert "| good_change | AGENT_FAIL | PASS | — | ✗ |" in capsys.readouterr().out.splitlines()


def test_cli_selfcheck_without_fixtures_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["selfcheck", "--dir", str(tmp_path)]) == 2
    assert "lgcov selfcheck:" in capsys.readouterr().err


def test_good_change_is_a_real_refactor_of_respond_only() -> None:
    # Whitespace wouldn't change the fingerprint, so serving would skip the node and the
    # fixture would prove nothing; a real refactor makes respond re-run live.
    import importlib.util

    from lgcov.fingerprint import fingerprint

    spec = importlib.util.spec_from_file_location(
        "good_change", SELFCHECK / "good_change" / "graph.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    baseline = json.loads((SELFCHECK / "cassettes" / "baseline.json").read_text(encoding="utf-8"))
    recorded = {s["node"]: s["code"] for s in baseline["runs"][0]["steps"]}
    assert fingerprint(module.respond) != recorded["respond"]
    assert fingerprint(module.classify) == recorded["classify"]


# T089 PR B: tiers. pr serves unchanged nodes, judges each case once and never runs a
# general LLM judge; nightly runs every node live with every judge, k = 3.
LLM_TAXONOMY = """\
version: 1
system: demo_agent
failure_modes:
  - id: respond_drift
    description: The answer loses the order status.
kpis:
  - name: respond_runs
    failure_mode: respond_drift
    node: respond
    judge: code
    description: Every run ends by responding.
    rubric: Did the run visit respond?
    check: {type: node_visited, node: respond}
  - name: answer_tone
    failure_mode: respond_drift
    node: respond
    judge: llm
    description: A general LLM judge, so nightly only.
    rubric: Is the answer polite?
"""


class _NoCallsJudge:
    """A general LLM judge client that fails any test that calls it."""

    def __init__(self, kpi: KPI) -> None:
        self.kpi = kpi

    def judge(self, node_input: NodeState, node_output: NodeState) -> Verdict:
        raise AssertionError("the pr tier called a general LLM judge")


def test_cli_ci_pr_tier_never_calls_a_general_llm_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("lgcov.cli._llm_judge", _NoCallsJudge)
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    (tmp_path / "taxonomy.yaml").write_text(LLM_TAXONOMY, encoding="utf-8")
    code, report, _ = _run_ci(tmp_path, "--taxonomy", "taxonomy.yaml")  # tier: pr
    assert code == 0
    assert "Nightly only (`judge: llm`): `answer_tone`." in report
    assert "| respond_runs | 2 | 100% | 100% |" in report
    assert "(0 live / 5 served)" in report and "· budget 90 s" in report
    assert "tier: `pr`" in report


def test_cli_ci_nightly_tier_runs_every_node_live_with_k_3_and_no_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    code, report, _ = _run_ci(tmp_path, "--taxonomy", DEMO_TAXONOMY, "--tier", "nightly")
    assert code == 0
    assert "(5 live / 0 served)" in report and "· no budget" in report
    assert "| answer_states_order_status (n=2, k=3) | 100% | 50% | ⚠️ 50% |" in report
    assert "tier: `nightly`" in report


def test_cli_ci_nightly_llm_kpi_without_a_client_is_infra_error_only_when_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    (tmp_path / "taxonomy.yaml").write_text(LLM_TAXONOMY, encoding="utf-8")
    args = ("--taxonomy", "taxonomy.yaml", "--tier", "nightly")
    code, report, _ = _run_ci(tmp_path, *args)
    assert code == 2  # answer_tone blocks: nothing judged it
    assert "⚠️ No llm client for `answer_tone`: INFRA_ERROR." in report
    (tmp_path / "lgcov.yaml").write_text("gate: {advisory_kpis: [answer_tone]}\n", encoding="utf-8")
    code, report, _ = _run_ci(tmp_path, *args)
    assert code == 0  # a misconfigured advisory judge doesn't fail every night
    assert "⚠️ Skipped, no llm client: `answer_tone` (advisory)." in report


def test_cli_ci_links_main_s_latest_nightly_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    url = "https://github.com/o/r/actions/runs/123"
    _, report, _ = _run_ci(tmp_path, "--nightly-url", url)
    assert f"Latest nightly run on main: {url}" in report
