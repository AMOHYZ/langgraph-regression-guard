import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from importlib.metadata import EntryPoint
from pathlib import Path
from unittest.mock import Mock

import pytest
from fixtures.langsmith_api import EXAMPLES, KEY, Fake, Reply

from examples.synthetic_agents import AGENTS
from lgcov.cassette import _codes, replay_run
from lgcov.cli import _l2_judge, _lgcov_root, _load_graph, main
from lgcov.jev_judge import JevJudge
from lgcov.judges import KPI, NodeState, Verdict
from lgcov.slices import Group
from lgcov.synthetic import Case, every_case
from lgcov.synthetic_score import METHODS, rates, votes_for
from lgcov.taxonomy import load_taxonomy


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
    assert capsys.readouterr().out == f"lgcov {version('lgcov')}\n"  # the distribution is lgcov


# `lgcov --help`, `lgcov -h` and bare `lgcov` showed graph-diff's usage: both scripts ran
# `main`. These tests run what pyproject.toml's [project.scripts] gives each script.
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
LGCOV_COMMANDS = (
    "record",
    "replay",
    "cassette accept",
    "ci",
    "verify-result",
    "snapshot",
    "coverage",
    "lint",
    "judge",
    "calibrate",
    "review override",
    "taxonomy check",
    "taxonomy draft",
    "taxonomy approve",
    "import langsmith",
    "round init",
    "round lock",
    "round check",
    "bench",
    "metrics summary",
    "selfcheck",
)


def _script(name: str) -> Callable[[list[str]], int]:
    """The function the console script `name` runs, resolved as its installed wrapper does."""
    scripts = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["scripts"]
    run: Callable[[list[str]], int] = EntryPoint(name, scripts[name], "console_scripts").load()
    return run


def _listed(command: str, text: str) -> bool:
    return any(line.lstrip().startswith(f"{command} ") for line in text.splitlines())


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_lgcov_help_lists_every_command(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert _script("lgcov")([flag]) == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: lgcov ")
    assert [c for c in (*LGCOV_COMMANDS, "--version") if not _listed(c, out)] == []


def test_bare_lgcov_prints_its_usage_and_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert _script("lgcov")([]) == 2  # no command: argparse's exit code for a missing argument
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("usage: lgcov ")
    assert [c for c in LGCOV_COMMANDS if not _listed(c, captured.err)] == []


@pytest.mark.parametrize("command", LGCOV_COMMANDS)
def test_lgcov_runs_each_command_its_help_lists(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        _script("lgcov")([*command.split(), "--help"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith(f"usage: lgcov {command.split()[0]}")


def test_lgcov_version_still_runs(capsys: pytest.CaptureFixture[str]) -> None:
    from importlib.metadata import version

    assert _script("lgcov")(["--version"]) == 0
    assert capsys.readouterr().out == f"lgcov {version('lgcov')}\n"


# Anything else is lgcov's error too, not graph-diff's. Two snapshot files are no lgcov
# command either: `graph-diff OLD NEW` diffs them.
@pytest.mark.parametrize(
    "argv",
    [["repaly"], ["--frobnicate"], [str(GOLDEN), str(GOLDEN)]],
    ids=["typo", "unknown option", "two snapshots"],
)
def test_lgcov_rejects_an_unknown_command(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _script("lgcov")(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"lgcov: unknown command {argv[0]!r} (lgcov --help lists the commands)\n"


def test_graph_diff_help_is_still_graph_diffs(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        _script("graph-diff")(["--help"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("usage: graph-diff [-h] old new\n")


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


# A graph file stays in sys.modules: Python, pydantic and LangGraph look a class's module up there.
_FUTURE = "from __future__ import annotations\n"
_TYPED_DICT_STATE = "class State(TypedDict):\n    messages: Annotated[list, add_messages]\n"


def _one_node(defs: str, answer: str, future: str = _FUTURE) -> str:
    return f"""{future}from dataclasses import dataclass
from typing import Annotated, Generic, Literal, Optional, TypedDict, TypeVar
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel
State = MessagesState
{defs}
def node(state: State) -> State:
    return {{"messages": [{{"role": "assistant", "content": {answer}}}]}}
graph = StateGraph(State).add_node(node).add_edge(START, "node").compile()
"""


_GENERIC = 'T = TypeVar("T")\nclass B(BaseModel, Generic[T]):\n    v: T\nC = B[int]\n'
_NEEDS_ITS_MODULE = {
    "dataclass": _one_node('@dataclass\nclass C:\n    text: str = "ok"\n', "C().text"),
    "pydantic_generic": _one_node(_GENERIC, "str(C(v=1).v)", future=""),
    "typed_dict_state": _one_node(_TYPED_DICT_STATE, '"ok"'),
    # This one fails inside the node, so being registered while it loads isn't enough.
    "pydantic_model": _one_node(
        'class C(BaseModel):\n    text: Optional[str] = "ok"\n', "C().text"
    ),
}
_CLASSES = 'class R(BaseModel):\n    text: str = "old"\nclass C:\n    reply = R\n'
_USES_CLASSES = _one_node(_CLASSES, "C.reply().text", future="")


def _one_node_project(folder: Path, source: str) -> Path:
    (folder / "graph.py").write_text(source, encoding="utf-8")
    (folder / "inputs.json").write_text('[{"messages": [["user", "hi"]]}]', encoding="utf-8")
    cfg = folder / "lgcov.yaml"
    entry = f"graph_entrypoint: {folder / 'graph.py'}:graph\n"
    cfg.write_text(f"{entry}cassette_dir: {folder / 'cassettes'}\n", encoding="utf-8")
    return cfg


@pytest.mark.parametrize("source", _NEEDS_ITS_MODULE.values(), ids=_NEEDS_ITS_MODULE)
def test_cli_runs_a_graph_file_that_needs_its_module(tmp_path: Path, source: str) -> None:
    cfg = _one_node_project(tmp_path, source)
    assert main(["record", "--config", str(cfg), "--inputs", str(tmp_path / "inputs.json")]) == 0
    assert main(["replay", "--live", "--config", str(cfg)]) == 0


# The state itself, and a ToolNode tool's arguments, as dataclasses whose hints are strings.
_FUTURE_GRAPHS = Path(__file__).parent / "fixtures" / "future_annotations.py"
_FUTURE_ANSWERS = {
    "dataclass_state": "dataclass state ok",
    "typed_dict_state": "typed dict state ok",
    # ToolNode turns a tool's error into its answer, so a crash would still record: check it ran.
    "tool_node": "Riyadh: sunny for 2 days",
}


@pytest.mark.parametrize(("attr", "answer"), _FUTURE_ANSWERS.items(), ids=_FUTURE_ANSWERS)
def test_cli_records_future_annotations_with_dataclasses(
    tmp_path: Path, attr: str, answer: str
) -> None:
    source = _FUTURE_GRAPHS.read_text(encoding="utf-8") + f"graph = {attr}\n"
    cfg = _one_node_project(tmp_path, source)
    assert main(["record", "--config", str(cfg), "--inputs", str(tmp_path / "inputs.json")]) == 0
    assert answer in (tmp_path / "cassettes" / "baseline.json").read_text(encoding="utf-8")
    assert main(["replay", "--live", "--config", str(cfg)]) == 0


_DATACLASS_IN_STATE = """import operator
from dataclasses import dataclass, field
from typing import Annotated
from langgraph.graph import START, StateGraph
@dataclass
class Note:
    text: str
@dataclass
class State:
    q: str = ""
    notes: Annotated[list[Note], operator.add] = field(default_factory=list)
def a(state: State) -> dict[str, object]:
    return {"notes": [Note(state.q)]}
def b(state: State) -> dict[str, object]:
    return {"notes": [Note(state.notes[0].text + "!")]}
graph = StateGraph(State).add_node(a).add_node(b).add_edge(START, "a").add_edge("a", "b").compile()
"""


def test_cli_replays_a_dataclass_value_in_the_state(tmp_path: Path) -> None:
    # The cassette saves Note as {"text": ...}; the fresh run's Note must compare equal to it.
    cfg = _one_node_project(tmp_path, _DATACLASS_IN_STATE)
    (tmp_path / "inputs.json").write_text('[{"q": "hi"}]', encoding="utf-8")
    assert main(["record", "--config", str(cfg), "--inputs", str(tmp_path / "inputs.json")]) == 0
    assert main(["replay", "--live", "--config", str(cfg)]) == 0
    assert main(["replay", "--config", str(cfg)]) == 0


def test_cli_ci_runs_head_against_head_after_loading_a_base_of_the_same_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ci loads head, then the base's graph.py, then runs head. pydantic looks `Side` up in
    # sys.modules at its first use, so it could find the base's and reject "head".
    reply = 'class R(BaseModel):\n    side: Side\nSide = Literal["head"]\n'
    source = _one_node(_TYPED_DICT_STATE + reply, 'R(side="head").side')
    monkeypatch.chdir(tmp_path)
    cfg = _one_node_project(tmp_path, source)
    assert main(["record", "--config", str(cfg), "--inputs", "inputs.json"]) == 0
    (tmp_path / "base").mkdir()
    base = source.replace('Literal["head"]', 'Literal["base"]')
    (tmp_path / "base" / "graph.py").write_text(base, encoding="utf-8")
    code, report, _ = _run_ci(tmp_path, "--base-dir", "base", "--live")
    assert code == 0
    assert "No graph changes." in report  # the base needs its module too, to load at all


def test_load_graph_leaves_a_package_named_like_the_file_importable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # src/agent/agent.py: registered as `agent`, the file would hide the package it imports,
    # and left out, its classes would be looked up in that package, where `Annotated` isn't.
    package = tmp_path / "acme_agent"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "state.py").write_text('TEXT = "ok"\n', encoding="utf-8")
    source = _one_node(_TYPED_DICT_STATE, "TEXT").replace(
        "from dataclasses", "from acme_agent.state import TEXT\nfrom dataclasses"
    )
    (package / "acme_agent.py").write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(tmp_path)
    assert _load_graph(f"{package / 'acme_agent.py'}:graph") is not None
    assert sys.modules["acme_agent"].__file__ == str(package / "__init__.py")


def test_load_graph_leaves_a_real_module_named_like_the_file_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "json", json)  # put back after the test, whatever happens
    path = tmp_path / "json.py"  # a class here counted as the stdlib's, so an edit went unseen
    path.write_text(_USES_CLASSES, encoding="utf-8")
    old = _codes(_load_graph(f"{path}:graph"))
    assert sys.modules["json"] is json
    path.write_text(_USES_CLASSES.replace('"old"', '"new"'), encoding="utf-8")
    assert _codes(_load_graph(f"{path}:graph")) != old


def test_load_graph_gives_a_node_the_input_its_annotation_names(tmp_path: Path) -> None:
    # LangGraph narrows a node's input from its annotation, and quietly gives up if it can't
    # resolve it. Left out of sys.modules, the node saw the whole state, unlike outside lgcov.
    defs = (
        "class State(TypedDict):\n    messages: list\n    secret: str\n"
        "class Asked(TypedDict):\n    secret: Optional[str]\n"
    )
    source = _one_node(defs, '",".join(sorted(state))').replace("state: State", "state: Asked")
    path = tmp_path / "graph.py"
    path.write_text(source, encoding="utf-8")
    done = _load_graph(f"{path}:graph").invoke({"messages": [], "secret": "s"})
    assert done["messages"][0]["content"] == "secret"


def test_load_graph_keeps_a_free_stem_and_the_fingerprint_from_before_it_was_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # fingerprint.py asks sys.modules, which now has the file. Its classes still count by what's
    # in them, so a cassette recorded before it was registered still matches, and edits count.
    import importlib.util

    from lgcov.fingerprint import fingerprint

    monkeypatch.delitem(sys.modules, "graph", raising=False)
    path, other = tmp_path / "graph.py", tmp_path / "other" / "graph.py"
    path.write_text(_USES_CLASSES, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("graph", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    recorded = fingerprint(module.node)
    monkeypatch.syspath_prepend(tmp_path)  # the file finds itself, which hides nothing
    assert _codes(_load_graph(f"{path}:graph"))["node"] == recorded is not None
    assert sys.modules["graph"].__file__ == str(path)
    # A later file by that name takes it: the earlier one was lgcov's own.
    other.parent.mkdir()
    other.write_text(_USES_CLASSES.replace('"old"', '"new"'), encoding="utf-8")
    assert _codes(_load_graph(f"{other}:graph"))["node"] != recorded
    assert sys.modules["graph"].__file__ == str(other)


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


# T177: mask_volatile_in_key reaches every command that replays.
@pytest.mark.parametrize(
    "command",
    [
        ["replay"],
        ["ci", "--report", "report.md", "--taxonomy", "taxonomy.yaml"],
        ["judge", "--taxonomy", "taxonomy.yaml"],
        [
            *("review", "override", "--kpi", "answer_states_order_status"),
            *("--case-id", "run-0/respond", "--judge-label", "pass", "--human-label", "fail"),
            *("--reviewer", "ahmed", "--taxonomy", "taxonomy.yaml"),
        ],
    ],
    ids=["replay", "ci", "judge", "review"],
)
def test_every_command_that_replays_reads_mask_volatile_in_key(
    command: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    (tmp_path / "taxonomy.yaml").write_text(
        Path(DEMO_TAXONOMY).read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "lgcov.yaml").write_text(
        "graph_entrypoint: graph.py:graph\ncassette_dir: cassettes\nmask_volatile_in_key: true\n",
        encoding="utf-8",
    )
    spy = Mock(wraps=replay_run)
    monkeypatch.setattr("lgcov.cli.replay_run", spy)
    main(command)
    assert spy.call_count > 0
    assert all(c.kwargs["mask_volatile"] is True for c in spy.call_args_list)


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


def test_cli_replay_still_reports_root_and_propagated_drift(
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
    assert "| `b` | propagated | 1/1 |" in lines


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
        "first-drift": "respond",  # T162
        "inconclusive-count": "0",
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


def test_cli_ci_fail_on_never_exits_0_but_asks_a_human_about_the_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    _ci_project(tmp_path, monkeypatch, demo.replace("a concise", "a friendly"))
    code, report, outputs = _run_ci(tmp_path, "--fail-on", "never")
    assert (code, outputs["verdict"], outputs["root-drift-count"]) == (0, "needs_human", "1")
    assert report.startswith("<!-- lgcov-report -->\n## ⚠️ No decision: needs_human\n")  # T153
    assert "| `respond` | root | 2/2 |" in report


def test_cli_ci_advisory_mode_reports_drift_and_says_it_does_not_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    _ci_project(tmp_path, monkeypatch, demo.replace("a concise", "a friendly"))
    code, report, outputs = _run_ci(tmp_path, "--mode", "advisory")
    assert (code, outputs["verdict"], outputs["exit-code"]) == (0, "fail", "0")
    assert report.startswith("<!-- lgcov-report -->\n## 🔴 lgcov: drift (advisory, not blocking)\n")
    assert "It does not block this merge and it does not approve this PR." in report
    assert "`lgcov cassette accept --nodes respond`" in report  # the real node, ready to run


def test_cli_ci_an_infra_error_stays_exit_2_in_advisory_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    code, report, outputs = _run_ci(tmp_path, "--mode", "advisory", "--harness-error", "boom")
    assert (code, outputs["verdict"]) == (2, "error")
    assert report.startswith("<!-- lgcov-report -->\n## ⚠️ lgcov: harness error, not a regression")
    assert "⚠️ Harness error, not a regression: lgcov could not finish" in report


def test_cli_ci_rejects_an_unknown_mode_or_ci_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    for bad in (("--mode", "shadow"), ("--ci-status", "green")):
        with pytest.raises(SystemExit):
            _run_ci(tmp_path, *bad)


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
    # T162: its code didn't change, so it isn't root; nothing explains the drift, so it blocks.
    assert "| `lookup_order` | inconclusive (drifted with no changed node before it:" in report


# T175: the PR changes one condition in the router: nothing in a node's code, so no node's
# fingerprint moves, and every run takes the other branch.
ROUTER_EDIT = ('if state["needs_tool"] else', 'if not state["needs_tool"] else')


def test_cli_ci_names_the_routing_node_root_when_the_pr_edits_its_router(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    assert demo.count(ROUTER_EDIT[0]) == 1
    _ci_project(tmp_path, monkeypatch, demo.replace(*ROUTER_EDIT))
    code, report, outputs = _run_ci(tmp_path)
    assert code == 1
    assert "| `classify` | root (routing change at classify) | 2/2 |" in report
    assert "| `lookup_order` | propagated | 2/2 |" in report
    assert "| `respond` | propagated | 2/2 |" in report
    assert "inconclusive" not in report.split("### Cassette replay")[1].split("###")[0]
    assert "Unchanged: `classify`" not in report
    assert outputs["inconclusive-count"] == "0"


def test_cli_ci_on_a_baseline_from_before_routers_were_kept_stays_inconclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    _ci_project(tmp_path, monkeypatch, demo.replace(*ROUTER_EDIT))
    baseline = tmp_path / "cassettes" / "baseline.json"
    old = json.loads(baseline.read_text(encoding="utf-8"))
    assert [s.pop("router", None) for r in old["runs"] for s in r["steps"]].count(None) == 3
    baseline.write_text(json.dumps(old), encoding="utf-8")  # as recorded before T175
    code, report, _ = _run_ci(tmp_path)
    assert code == 1
    assert "| `lookup_order` | inconclusive (drifted with no changed node before it:" in report
    assert "routing change" not in report


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
    bands = str(tmp_path / "bands.json")  # T063: absent unless a test writes it
    return [
        "judge",
        "--graph",
        f"{DEMO_GRAPH}:graph",
        "--cassette-dir",
        cassettes,
        "--bands",
        bands,
    ]


def _bands(path: Path, **judges: str) -> None:
    """T063: a calibration/bands.json giving these KPIs these bands."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "jev_model": None, "judges": {k: {"band": b} for k, b in judges.items()}}
    path.write_text(json.dumps(doc), encoding="utf-8")


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
        "| answer_states_order_status (advisory) | 2 | 83% | 83% | +0.00 [+0.00, +0.00] | 1.000 "
        "| 0.05 | — | 74 | ⚠️ INCONCLUSIVE | — |"
    ) in out  # T063: no calibration labels, so advisory; T150: 2 identical cases, need 74
    assert "need 72 more cases for respond_runs (N*=74)" in out
    assert "⚠️ 1 of 2 judges uncalibrated: no model judge can block this PR." in out
    # T090: per node and slice; only blocking judges' cases are pooled into the verdict
    assert "#### Node `respond`" in out
    assert "| `respond_drift` | `respond_runs`; not pooled: `answer_states_order_status` |" in out
    assert "Overall: ⚠️ INCONCLUSIVE, from" in out  # exit 0 all the same


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
    _bands(tmp_path / "bands.json", answer_states_order_status="blocking")  # T063: calibrated
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


# T195: `lgcov judge --from-runs` judges the pair `lgcov bench --keep-runs` saved. Nothing is
# replayed, so no graph is loaded and the agent makes no call.
SAVED_PAIR = FIXTURES / "bench_runs" / "open_deep_research-163"
SAVED_TAXONOMY = """\
version: 1
system: open_deep_research
failure_modes:
  - id: unsupported_claim
    description: The report says something its notes don't back.
kpis:
  - name: report_claims_supported
    failure_mode: unsupported_claim
    node: final_report_generation
    judge: fake
    description: The 2nd report judged fails; with k=1 that is head's.
    rubric: Is every claim in the report backed by the notes?
    check: {fail_every: "2"}
"""


def _saved_args(tmp_path: Path, runs: Path = SAVED_PAIR) -> list[str]:
    taxonomy = tmp_path / "taxonomy.yaml"
    taxonomy.write_text(SAVED_TAXONOMY, encoding="utf-8")
    bands = str(tmp_path / "bands.json")
    return ["judge", "--from-runs", str(runs), "--taxonomy", str(taxonomy), "--bands", bands]


def test_cli_judge_from_runs_judges_the_saved_pair_without_a_graph(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_graph(target: str) -> object:
        raise AssertionError(f"--from-runs loaded a graph: {target}")

    monkeypatch.setattr("lgcov.cli._load_graph", no_graph)
    assert main(_saved_args(tmp_path)) == 0  # no --graph, no cassette dir
    out = capsys.readouterr().out
    assert "Planned: 2 judge calls, 2 to model judges (--max-calls 200)" in out
    assert "| report_claims_supported (advisory) | 1 | 100% | 0% |" in out


def test_cli_judge_from_runs_refuses_live_and_a_folder_without_the_pair(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*_saved_args(tmp_path), "--live"]) == 2
    assert "--from-runs runs nothing" in capsys.readouterr().err
    assert main(_saved_args(tmp_path, tmp_path / "empty")) == 2
    assert "base.json" in capsys.readouterr().err


def test_cli_judge_from_runs_refuses_sides_with_different_run_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, runs in (("base.json", 1), ("head.json", 0)):
        saved = json.loads((SAVED_PAIR / name).read_text(encoding="utf-8"))
        saved["runs"] = saved["runs"][:runs]
        (tmp_path / name).write_text(json.dumps(saved), encoding="utf-8")
    assert main(_saved_args(tmp_path, tmp_path)) == 2
    assert "1 runs on base, 0 on head" in capsys.readouterr().err


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
    _bands(tmp_path / "calibration" / "bands.json", answer_states_order_status="blocking")
    code, report, outputs = _run_ci(tmp_path, "--taxonomy", "taxonomy.yaml")
    assert (code, outputs["verdict"]) == (0, "needs_human")
    assert report.startswith("<!-- lgcov-report -->\n## ⚠️ No decision: needs_human\n")
    assert "That is not a pass." in report and "verdict: **needs_human**" in report  # T153
    assert "Statistical verdict: INCONCLUSIVE" in report
    assert "| answer_states_order_status | 2 | 100% | 50% |" in report
    assert "| ⚠️ underpowered: need 6 more cases for answer_states_order_status" in report
    code, report, outputs = _run_ci(
        tmp_path, "--taxonomy", "taxonomy.yaml", "--fail-on-inconclusive"
    )
    assert (code, outputs["verdict"]) == (1, "needs_human")  # T153: fails the check, finds nothing
    assert report.startswith("<!-- lgcov-report -->\n## ⚠️ No decision: needs_human\n")
    assert "that is not a finding against the agent" in report


def test_cli_ci_records_approval_and_other_ci_without_changing_the_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    (tmp_path / "taxonomy.yaml").write_text(UNDECIDED_TAXONOMY, encoding="utf-8")
    _bands(tmp_path / "calibration" / "bands.json", answer_states_order_status="blocking")
    taxonomy = ("--taxonomy", "taxonomy.yaml")
    code, plain, outputs = _run_ci(tmp_path, *taxonomy)
    told = ("--approved-by", "alice, bob", "--ci-status", "failure")
    told_code, report, told_outputs = _run_ci(tmp_path, *taxonomy, *told)
    assert (told_code, told_outputs["verdict"]) == (code, outputs["verdict"]) == (0, "needs_human")
    assert report.splitlines()[1] == plain.splitlines()[1]  # the headline
    assert (
        "Statistical verdict: INCONCLUSIVE · Human approval: `alice`, `bob` (recorded; it does "
        "not change the verdict) · Other CI: failure"
    ) in report
    assert "Fix the failing CI first. `alice`, `bob` approved, so the merge is their" in report


def test_cli_ci_inconclusive_blocks_in_lgcov_yaml_fails_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    (tmp_path / "taxonomy.yaml").write_text(UNDECIDED_TAXONOMY, encoding="utf-8")
    _bands(tmp_path / "calibration" / "bands.json", answer_states_order_status="blocking")
    (tmp_path / "lgcov.yaml").write_text("gate:\n  inconclusive_blocks: true\n", encoding="utf-8")
    code, report, outputs = _run_ci(tmp_path, "--taxonomy", "taxonomy.yaml")
    assert (code, outputs["verdict"]) == (1, "needs_human")
    assert report.startswith("<!-- lgcov-report -->\n## ⚠️ No decision: needs_human\n")


# T153: a code judge, identical on both sides, with a margin wide enough for 4 cases to SHIP.
SHIPPING_TAXONOMY = """\
version: 1
system: demo_agent
failure_modes:
  - id: respond_drift
    description: The respond node stops answering.
kpis:
  - name: respond_runs
    failure_mode: respond_drift
    node: respond
    judge: code
    margin: 0.95
    description: Every run ends by responding.
    rubric: Did the run visit respond?
    check: {type: node_visited, node: respond}
"""


def _ship_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The demo with its 2 runs recorded twice over: 4 cases, enough for SHIPPING_TAXONOMY."""
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    (tmp_path / "taxonomy.yaml").write_text(SHIPPING_TAXONOMY, encoding="utf-8")
    baseline = tmp_path / "cassettes" / "baseline.json"
    doc = json.loads(baseline.read_text(encoding="utf-8"))
    baseline.write_text(json.dumps({"runs": doc["runs"] * 2}), encoding="utf-8")


def test_cli_ci_a_ship_says_what_it_covers_and_a_served_only_one_says_nothing_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ship_project(tmp_path, monkeypatch)
    taxonomy = ("--taxonomy", "taxonomy.yaml")
    code, report, outputs = _run_ci(tmp_path, *taxonomy)
    assert (code, outputs["verdict"]) == (0, "pass")
    assert report.startswith(
        "<!-- lgcov-report -->\n## 🟢 lgcov: every node served, nothing re-ran\n"
    )
    assert "Statistical verdict: SHIP" in report and "(0 live / " in report
    code, report, outputs = _run_ci(tmp_path, *taxonomy, "--live")
    assert (code, outputs["verdict"]) == (0, "pass")
    assert report.startswith("<!-- lgcov-report -->\n## 🟢 lgcov: SHIP (1 blocking KPI)\n")


def test_cli_ci_a_blocking_group_that_does_not_ship_is_no_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A few cases can't pool into a real one; tests/test_verdict_actions.py builds that from 120.
    _ship_project(tmp_path, monkeypatch)
    undecided = Group("respond", "", ("respond_runs",), True, None, 0.05, None, None, "needs_human")
    taxonomy = ("--taxonomy", "taxonomy.yaml")
    assert _run_ci(tmp_path, *taxonomy)[2]["verdict"] == "pass"  # the KPI SHIPs on its own
    monkeypatch.setattr("lgcov.cli._groups", lambda *_: [undecided])
    code, report, outputs = _run_ci(tmp_path, *taxonomy)
    assert (code, outputs["verdict"]) == (0, "needs_human")
    assert report.startswith("<!-- lgcov-report -->\n## ⚠️ No decision: needs_human\n")
    assert "Statistical verdict: INCONCLUSIVE" in report
    code, _, outputs = _run_ci(tmp_path, *taxonomy, "--fail-on-inconclusive")
    assert (code, outputs["verdict"]) == (1, "needs_human")


ORDER_LOOKUP_TAXONOMY = """\
version: 1
system: demo_agent
failure_modes:
  - id: skips_the_lookup
    description: An order question no longer reaches the order lookup.
kpis:
  - name: order_lookup_runs
    failure_mode: skips_the_lookup
    node: lookup_order
    judge: code
    description: Every run looks up the order.
    rubric: Did the run visit lookup_order?
    check: {type: node_visited, node: lookup_order}
"""


def _order_lookup_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, questions: list[str]
) -> None:
    """The demo with these questions really recorded, gated on a code check."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "graph.py").write_text(DEMO_GRAPH.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "taxonomy.yaml").write_text(ORDER_LOOKUP_TAXONOMY, encoding="utf-8")
    (tmp_path / "lgcov.yaml").write_text(
        "graph_entrypoint: graph.py:graph\ncassette_dir: cassettes\n", encoding="utf-8"
    )
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps([{"question": q} for q in questions]), encoding="utf-8")
    assert main(["record", "--inputs", str(inputs)]) == 0


def _skipping_the_lookup_for(marker: str) -> str:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    src = demo.replace(
        'return {"needs_tool": "order" in state["question"].lower()}',
        'q = state["question"].lower()\n'
        f'    return {{"needs_tool": "order" in q and {marker!r} not in q}}',
    )
    assert src != demo
    return src


def test_cli_ci_one_new_code_failure_in_200_blocks_though_the_test_says_ship(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = [f"Where is my order {i}?" for i in range(199)] + ["Where is my order 999?"]
    _order_lookup_project(tmp_path, monkeypatch, questions)
    (tmp_path / "graph.py").write_text(_skipping_the_lookup_for("999"), encoding="utf-8")
    never = ("--taxonomy", "taxonomy.yaml", "--fail-on", "never")
    code, report, outputs = _run_ci(tmp_path, *never)
    assert (code, outputs["verdict"]) == (1, "fail")
    assert report.startswith("<!-- lgcov-report -->\n## 🔴 lgcov: code check failed\n")
    assert (
        "`order_lookup_runs` passed on the base and fails here on 1 of 200 cases "
        "(`run-199/lookup_order`)."
    ) in report
    assert "Statistical verdict: SHIP" in report  # what the test says stays as it was
    assert "| 🔴 new failure: order_lookup_runs on 1 of 200 cases" in report
    code, report, outputs = _run_ci(tmp_path, *never, "--mode", "advisory")
    assert (code, outputs["verdict"]) == (0, "fail")
    assert "## 🔴 lgcov: code check failed (advisory, not blocking)\n" in report


def test_cli_ci_a_new_code_failure_is_named_over_the_drift_it_comes_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = ["Where is my order 1?", "Where is my order 999?"]
    _order_lookup_project(tmp_path, monkeypatch, questions)
    (tmp_path / "graph.py").write_text(_skipping_the_lookup_for("999"), encoding="utf-8")
    code, report, _ = _run_ci(tmp_path, "--taxonomy", "taxonomy.yaml")
    assert code == 1
    assert report.startswith("<!-- lgcov-report -->\n## 🔴 lgcov: code check failed\n")
    assert "`lgcov cassette accept" not in report  # that would erase the failure


def test_cli_ci_a_code_check_in_advisory_kpis_reports_the_drop_and_does_not_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = ["Where is my order 1?", "Where is my order 999?"]
    _order_lookup_project(tmp_path, monkeypatch, questions)
    (tmp_path / "graph.py").write_text(_skipping_the_lookup_for("999"), encoding="utf-8")
    (tmp_path / "lgcov.yaml").write_text(
        "graph_entrypoint: graph.py:graph\ncassette_dir: cassettes\n"
        "gate: {advisory_kpis: [order_lookup_runs]}\n",
        encoding="utf-8",
    )
    code, report, outputs = _run_ci(tmp_path, "--taxonomy", "taxonomy.yaml", "--fail-on", "never")
    assert (code, outputs["verdict"]) == (0, "needs_human")
    assert "code check failed" not in report
    assert "| ⚠️ new failure: order_lookup_runs on 1 of 2 cases" in report  # still reported


def test_cli_ci_a_check_that_already_failed_on_the_base_is_not_a_new_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _order_lookup_project(tmp_path, monkeypatch, ["Where is my order?", "What are your hours?"])
    code, report, outputs = _run_ci(tmp_path, "--taxonomy", "taxonomy.yaml", "--live")
    assert (code, outputs["verdict"]) == (0, "needs_human")  # 2 cases can't SHIP
    assert "code check failed" not in report and "new failure" not in report


# T181: tool checks see the tools a run called, read from the AI messages it recorded.
TOOL_TAXONOMY = """\
version: 1
system: demo_agent
failure_modes:
  - id: wrong_tool
    description: An order question stops calling the order lookup, or calls a tool it must not.
kpis:
  - name: looks_up_the_order
    failure_mode: wrong_tool
    node: lookup_order
    judge: code
    description: Every order question calls the lookup_order tool.
    rubric: Did the run call lookup_order?
    check: {type: tool_called, tool: lookup_order}
  - name: never_refunds
    failure_mode: wrong_tool
    node: lookup_order
    judge: code
    description: No run calls the refund_order tool.
    rubric: Did the run call refund_order?
    check: {type: tool_not_called, tool: refund_order}
"""
WHOLE_RUN = "node scope not applied to tool checks yet (whole-run)"


def _calling_tools(for_999: tuple[str, ...] = ("lookup_order",)) -> str:
    """The demo with lookup_order asking for its tool in an AIMessage, as a model would, and
    for the tools in `for_999` instead when the question has 999 in it."""
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    src = (
        demo.replace(
            "from typing import Literal, TypedDict\n",
            "from typing import Annotated, Literal, TypedDict\n\n"
            "from langchain_core.messages import AIMessage, AnyMessage\n"
            "from langgraph.graph.message import add_messages\n",
        )
        .replace(
            "    answer: str\n",
            "    answer: str\n    messages: Annotated[list[AnyMessage], add_messages]\n",
        )
        .replace(
            '    return {"tool_result": "order #42: shipped"}',
            f'    tools = {list(for_999)!r} if "999" in state["question"] else ["lookup_order"]\n'
            '    calls = [{"name": t, "args": {}, "id": f"call_{t}"} for t in tools]\n'
            '    asked = AIMessage("", id="ai-1", tool_calls=calls)\n'
            '    return {"tool_result": "order #42: shipped", "messages": [asked]}',
        )
    )
    assert "add_messages]" in src and "asked = AIMessage" in src and "Annotated, " in src
    return src


def _tool_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two order questions really recorded on the tool-calling demo, gated on tool checks."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "graph.py").write_text(_calling_tools(), encoding="utf-8")
    (tmp_path / "taxonomy.yaml").write_text(TOOL_TAXONOMY, encoding="utf-8")
    (tmp_path / "lgcov.yaml").write_text(
        "graph_entrypoint: graph.py:graph\ncassette_dir: cassettes\n", encoding="utf-8"
    )
    questions = ["Where is my order 1?", "Where is my order 999?"]
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps([{"question": q} for q in questions]), encoding="utf-8")
    assert main(["record", "--inputs", str(inputs)]) == 0


def test_cli_ci_a_forbidden_tool_call_fails_its_code_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tool_project(tmp_path, monkeypatch)
    forbidden = _calling_tools(("lookup_order", "refund_order"))
    (tmp_path / "graph.py").write_text(forbidden, encoding="utf-8")
    code, report, outputs = _run_ci(tmp_path, "--taxonomy", "taxonomy.yaml", "--fail-on", "never")
    assert (code, outputs["verdict"]) == (1, "fail")
    assert report.startswith("<!-- lgcov-report -->\n## 🔴 lgcov: code check failed\n")
    assert (
        "`never_refunds` passed on the base and fails here on 1 of 2 cases (`run-1/lookup_order`)."
    ) in report
    assert "looks_up_the_order" not in report.split("passed on the base")[0]
    assert WHOLE_RUN in report


def test_cli_ci_lookup_order_calling_its_tool_passes_tool_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tool_project(tmp_path, monkeypatch)
    code, report, _ = _run_ci(tmp_path, "--taxonomy", "taxonomy.yaml", "--fail-on", "never")
    assert code == 0 and "code check failed" not in report and "new failure" not in report
    # It passes on the base: so a PR that stops calling the tool is a new failure.
    (tmp_path / "graph.py").write_text(_calling_tools(for_999=()), encoding="utf-8")
    code, report, outputs = _run_ci(tmp_path, "--taxonomy", "taxonomy.yaml", "--fail-on", "never")
    assert (code, outputs["verdict"]) == (1, "fail")
    assert (
        "`looks_up_the_order` passed on the base and fails here on 1 of 2 cases "
        "(`run-1/lookup_order`)."
    ) in report


def test_cli_judge_exits_1_on_a_forbidden_tool_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _tool_project(tmp_path, monkeypatch)
    forbidden = _calling_tools(("lookup_order", "refund_order"))
    (tmp_path / "graph.py").write_text(forbidden, encoding="utf-8")
    capsys.readouterr()
    args = ["judge", "--taxonomy", "taxonomy.yaml", "--bands", str(tmp_path / "bands.json")]
    assert main(args) == 1
    out = capsys.readouterr().out
    assert "| 🔴 new failure: never_refunds on 1 of 2 cases: `run-1/lookup_order` |" in out
    assert WHOLE_RUN in out


# T199: the tally line under the comment's heading. PR 7, the demo PR, makes this change.
PR_7 = ("a concise support", "a friendly, detailed support")


def test_cli_ci_tally_line_for_the_demo_prompt_tweak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    _ci_project(tmp_path, monkeypatch, demo)
    _, report, _ = _run_ci(tmp_path)
    assert report.splitlines()[3] == "0 of 2 runs touched · no KPI judged · root node: none"
    tweaked = demo.replace(*PR_7)
    assert tweaked != demo
    (tmp_path / "graph.py").write_text(tweaked, encoding="utf-8")
    _, report, _ = _run_ci(tmp_path)
    assert report.splitlines()[1:4] == [
        "## 🔴 lgcov: drift",
        "",
        "2 of 2 runs touched · no KPI judged · root node: `respond`",
    ]
    # With the demo's taxonomy: neither KPI reads the answer, so the prompt tweak can't lower one.
    _, report, _ = _run_ci(tmp_path, "--taxonomy", DEMO_TAXONOMY)
    assert report.splitlines()[3] == "2 of 2 runs touched · 0 worse · root node: `respond`"


def test_cli_ci_tally_line_counts_kpi_drops_and_names_the_first_runs_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _order_lookup_project(tmp_path, monkeypatch, ["Where is my order 1?", "Where is my order 999?"])
    skipping = _skipping_the_lookup_for("999")
    (tmp_path / "graph.py").write_text(skipping, encoding="utf-8")
    _, report, _ = _run_ci(tmp_path, "--taxonomy", "taxonomy.yaml")
    want = "1 of 2 runs touched · 1 worse (`order_lookup_runs` 1) · root node: `classify`"
    assert report.splitlines()[3] == want
    tweaked = skipping.replace(*PR_7)
    assert tweaked != skipping
    (tmp_path / "graph.py").write_text(tweaked, encoding="utf-8")
    _, report, _ = _run_ci(tmp_path, "--taxonomy", "taxonomy.yaml")
    # Run 1's root comes first; the drift table's sorted list would put `classify` first.
    want = "2 of 2 runs touched · 1 worse (`order_lookup_runs` 1) · root node: `respond` +1 more"
    assert report.splitlines()[3] == want


@pytest.mark.parametrize(
    "stop",
    [("--on-divergence", "fail"), ("--budget-seconds", "0")],
    ids=["on-divergence fail", "a budget hit"],
)
def test_cli_ci_a_run_replay_stopped_early_is_not_a_failed_code_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: tuple[str, str]
) -> None:
    _ci_project(tmp_path, monkeypatch, _rewriting_classify())
    (tmp_path / "taxonomy.yaml").write_text(SHIPPING_TAXONOMY, encoding="utf-8")
    baseline = tmp_path / "cassettes" / "baseline.json"
    doc = json.loads(baseline.read_text(encoding="utf-8"))
    baseline.write_text(
        json.dumps({"runs": doc["runs"] * 4}), encoding="utf-8"
    )  # 8: enough to BLOCK
    code, report, outputs = _run_ci(
        tmp_path, "--taxonomy", "taxonomy.yaml", "--fail-on", "never", *stop
    )
    assert (code, outputs["verdict"]) == (0, "needs_human")
    assert report.startswith("<!-- lgcov-report -->\n## ⚠️ No decision: needs_human\n")
    assert "lgcov did not check them." in report
    assert "KPI regression" not in report and "code check failed" not in report


def test_cli_ci_a_run_replay_stopped_early_is_no_case_for_a_final_kpi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, _rewriting_classify())
    taxonomy = UNDECIDED_TAXONOMY.replace("node: respond", "node: final")
    assert taxonomy != UNDECIDED_TAXONOMY
    (tmp_path / "taxonomy.yaml").write_text(taxonomy, encoding="utf-8")
    _bands(tmp_path / "calibration" / "bands.json", answer_states_order_status="blocking")
    args = ("--taxonomy", "taxonomy.yaml", "--fail-on", "never", "--on-divergence", "fail")
    _, report, _ = _run_ci(tmp_path, *args)
    assert "| answer_states_order_status | 0 |" in report  # its last step isn't the final one
    _, report, _ = _run_ci(tmp_path, *args, "--max-calls", "0")
    assert "refusing to make" not in report  # nor is its base side judged: no wasted model call


def test_cli_ci_a_run_replay_stopped_early_keeps_its_case_for_a_node_that_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, _rewriting_classify())
    taxonomy = UNDECIDED_TAXONOMY.replace("node: respond", "node: classify")
    assert taxonomy != UNDECIDED_TAXONOMY
    (tmp_path / "taxonomy.yaml").write_text(taxonomy, encoding="utf-8")
    _bands(tmp_path / "calibration" / "bands.json", answer_states_order_status="blocking")
    args = ("--taxonomy", "taxonomy.yaml", "--fail-on", "never", "--on-divergence", "fail")
    _, report, _ = _run_ci(tmp_path, *args)
    assert "Not checked (on-divergence: fail)" in report
    assert "| answer_states_order_status | 2 |" in report  # classify ran before the stop


def test_cli_judge_exits_1_on_a_new_code_failure_and_says_which(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    taxonomy = tmp_path / "taxonomy.yaml"
    taxonomy.write_text(ORDER_LOOKUP_TAXONOMY, encoding="utf-8")
    graph = tmp_path / "graph.py"
    graph.write_text(_skipping_the_lookup_for("order"), encoding="utf-8")  # never looks it up
    args = [*_judge_args(tmp_path), "--taxonomy", str(taxonomy), "--graph", f"{graph}:graph"]
    assert main(args) == 1
    assert "| 🔴 new failure: order_lookup_runs on 1 of 2 cases: `run-0/lookup_order` |" in (
        capsys.readouterr().out
    )


def test_cli_ci_refuses_more_model_judge_calls_than_max_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    (tmp_path / "taxonomy.yaml").write_text(UNDECIDED_TAXONOMY, encoding="utf-8")
    code, report, _ = _run_ci(tmp_path, "--taxonomy", "taxonomy.yaml", "--max-calls", "3")
    assert code == 2  # 4 model judge calls planned: a harness limit, not a regression
    assert "refusing to make 4 model judge calls" in report


def test_cli_ci_a_finding_stands_when_the_kpi_gate_then_cannot_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    _ci_project(tmp_path, monkeypatch, demo.replace("a concise", "a friendly"))
    (tmp_path / "taxonomy.yaml").write_text(UNDECIDED_TAXONOMY, encoding="utf-8")
    args = ("--taxonomy", "taxonomy.yaml", "--max-calls", "3")  # the gate refuses to start
    code, report, outputs = _run_ci(tmp_path, *args)
    assert (code, outputs["verdict"]) == (1, "fail")  # the drift replay found, not the harness
    assert report.startswith("<!-- lgcov-report -->\n## 🔴 lgcov: drift\n")
    assert "What it found before that stands; anything after it was not checked." in report
    assert "this PR was not checked" not in report  # T153: replay did check it
    assert "refusing to make 4 model judge calls" in report
    code, _, outputs = _run_ci(tmp_path, *args, "--mode", "advisory")
    assert (code, outputs["verdict"]) == (2, "fail")  # only the infra failure is left to report


def test_cli_ci_inconclusive_with_an_infra_error_exits_2_even_with_the_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, _rewriting_classify())
    (tmp_path / "taxonomy.yaml").write_text(UNDECIDED_TAXONOMY, encoding="utf-8")
    args = (
        "--taxonomy",
        "taxonomy.yaml",
        "--max-calls",  # the gate refuses to start, after replay left nodes not checked
        "1",
        "--on-divergence",
        "fail",
        "--fail-on",
        "never",
        "--fail-on-inconclusive",
    )
    code, report, outputs = _run_ci(tmp_path, *args)
    assert (code, outputs["verdict"]) == (2, "error")  # T153: was 1, an infra error called a fail
    assert report.startswith("<!-- lgcov-report -->\n## ⚠️ lgcov: harness error, not a regression")


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


# T152: the demo's `respond` raises these in turn, under a retry policy.
def _respond_raising(raises: str, rule: str = "") -> str:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    src = (
        demo.replace(
            "def respond(state: State) -> State:\n",
            f"RAISES = {raises}\n\n\n"
            "def respond(state: State) -> State:\n    if RAISES:\n        raise RAISES.pop()\n",
        )
        .replace(
            'b.add_node("respond", respond)',
            'b.add_node("respond", respond, retry_policy=RetryPolicy('
            f"initial_interval=0.0, jitter=False{rule}))",
        )
        .replace(
            "b = StateGraph(State)",
            "from langgraph.types import RetryPolicy\nb = StateGraph(State)",
        )
    )
    assert "RAISES" in src and "RetryPolicy(" in src
    return src


RAISED_LINE = (
    "Raised, and the run went on (a retry policy or error handler): `respond` in run 1 (1x). "
    "What a failed attempt did before raising, like a write, still happened."
)


def test_cli_ci_names_a_node_that_raised_and_ran_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, _respond_raising('[ConnectionError("down")]'))
    code, report, _ = _run_ci(tmp_path)
    assert code == 0  # it recovered, and its answer is the recorded one
    assert RAISED_LINE in report


def test_cli_replay_names_a_node_that_raised_and_ran_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _ci_project(tmp_path, monkeypatch, _respond_raising('[ConnectionError("down")]'))
    assert main(["replay", "graph.py:graph", "cassettes/baseline.json"]) == 0
    assert RAISED_LINE in capsys.readouterr().out


@pytest.mark.parametrize(
    ("raises", "rule", "code", "says"),
    [
        pytest.param(
            '[ValueError("bad")] * 3',
            "",
            1,
            "Node `respond` raised ValueError: bad\n",
            id="the_default_rule_refuses_it",
        ),
        pytest.param(
            '[ValueError("bad")] * 3',
            ", retry_on=ValueError",
            1,
            "Node `respond` raised ValueError: bad (3 failed attempts in this run)",
            id="its_own_rule_retries_it",
        ),
        pytest.param(
            '[ConnectionError("down")] * 3',
            "",
            2,
            "ConnectionError: down (3 failed attempts in this run)",
            id="network_trouble_to_the_last_attempt",
        ),
    ],
)
def test_cli_ci_says_how_many_attempts_a_failed_node_made(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raises: str,
    rule: str,
    code: int,
    says: str,
) -> None:
    _ci_project(tmp_path, monkeypatch, _respond_raising(raises, rule))
    got, report, _ = _run_ci(tmp_path)
    assert got == code
    assert says in report


def test_cli_ci_divergence_fail_leaves_the_rest_of_the_run_not_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, _rewriting_classify())
    args = ("--on-divergence", "fail", "--fail-on", "never")
    code, report, outputs = _run_ci(tmp_path, *args)
    assert (code, outputs["verdict"]) == (0, "needs_human")  # INCONCLUSIVE exits 0 by default
    assert report.startswith("<!-- lgcov-report -->\n## ⚠️ No decision: needs_human\n")
    assert "or review `lookup_order`, `respond` yourself: lgcov did not check them." in report
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
    # T109: routers and subgraph nodes are named too
    assert "| broken_router | AGENT_FAIL @ route | AGENT_FAIL | route | ✓ |" in out
    assert "| broken_subgraph | AGENT_FAIL @ sub.inner | AGENT_FAIL | sub.inner | ✓ |" in out


def test_cli_ci_a_router_that_raises_is_an_agent_failure_naming_the_router(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    broken = demo.replace(
        'return "lookup_order" if state["needs_tool"] else "respond"',
        'raise KeyError("needs_tool")',
    )
    assert broken != demo
    _ci_project(tmp_path, monkeypatch, broken)
    code, report, outputs = _run_ci(tmp_path, "--fail-on", "never")
    assert (code, outputs["verdict"]) == (1, "fail")
    assert "Node `route` raised KeyError" in report


def test_cli_ci_a_router_returning_an_unmapped_key_is_an_agent_failure_naming_the_router(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    typo = demo.replace(  # "lookup" is not in route's Literal["lookup_order", "respond"]
        'return "lookup_order" if state["needs_tool"] else "respond"',
        'return "lookup" if state["needs_tool"] else "respond"',
    )
    assert typo != demo
    _ci_project(tmp_path, monkeypatch, typo)
    code, report, outputs = _run_ci(tmp_path, "--fail-on", "never")
    assert (code, outputs["verdict"]) == (1, "fail")
    assert "Node `route` raised KeyError" in report


def test_cli_ci_a_router_timeout_is_a_harness_error_naming_the_router(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    slow = demo.replace(
        'return "lookup_order" if state["needs_tool"] else "respond"',
        'raise TimeoutError("router timed out")',
    )
    _ci_project(tmp_path, monkeypatch, slow)
    code, report, _ = _run_ci(tmp_path)
    assert code == 2
    assert "⚠️ Harness error (transport) in node `route`, not a regression" in report


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
    _bands(tmp_path / "calibration" / "bands.json", answer_tone="blocking")  # T063: calibrated
    args = ("--taxonomy", "taxonomy.yaml", "--tier", "nightly")
    code, report, _ = _run_ci(tmp_path, *args)
    assert code == 2  # answer_tone blocks: nothing judged it
    assert "⚠️ No llm client for `answer_tone`: INFRA_ERROR." in report
    (tmp_path / "lgcov.yaml").write_text("gate: {advisory_kpis: [answer_tone]}\n", encoding="utf-8")
    code, report, _ = _run_ci(tmp_path, *args)
    assert code == 0  # a misconfigured advisory judge doesn't fail every night
    assert "⚠️ Skipped, no llm client: `answer_tone` (advisory)." in report


def test_cli_ci_writes_its_timing_as_json_for_the_benchmark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # T105: the speed benchmark reads each run's phases here, unrounded.
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    _run_ci(tmp_path, "--timing-out", "out/timing.json")
    timing = json.loads((tmp_path / "out" / "timing.json").read_text(encoding="utf-8"))
    assert (timing["live"], timing["served"], timing["budget"]) == (0, 5, 90.0)
    assert timing["total"] >= timing["agent"] >= 0


def test_cli_ci_links_main_s_latest_nightly_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    url = "https://github.com/o/r/actions/runs/123"
    _, report, _ = _run_ci(tmp_path, "--nightly-url", url)
    assert f"Latest nightly run on main: {url}" in report


# T063: `lgcov calibrate` on the demo's 40 synthetic labels.
DEMO_LABELS = Path(__file__).resolve().parents[1] / "calibration" / "labels" / "demo.csv"


def test_cli_calibrate_prints_a_row_per_judge_in_plain_words(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["calibrate", str(DEMO_LABELS), "--bands", str(tmp_path / "bands.json")]) == 0
    out = capsys.readouterr().out
    assert "| TPR: catches human-failed cases | TNR: clears human-passed cases |" in out
    assert "| 40 | 0.90 [0.70, 0.97] | 0.95 [0.76, 0.99] |" in out
    assert "Threshold, answer_states_order_status: too few labels to tune (40 < 50)." in out


def test_cli_calibrate_counts_overrides_it_cannot_map(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    overrides = tmp_path / "calibration.jsonl"
    overrides.write_text('{"human_label": 1.0, "node": "respond"}\n', encoding="utf-8")
    bands = str(tmp_path / "bands.json")
    assert (
        main(["calibrate", str(DEMO_LABELS), "--overrides", str(overrides), "--bands", bands]) == 0
    )
    assert "1 override skipped: no kpi or judge_label" in capsys.readouterr().out


def test_cli_calibrate_with_a_missing_file_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["calibrate", "no-such-labels.csv"]) == 2
    assert "lgcov calibrate:" in capsys.readouterr().err


# T063 PR B: bands from calibration decide which judges can block.
def test_cli_calibrate_writes_bands_json_with_the_pinned_jev_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "lgcov.yaml"
    config.write_text("graph_entrypoint: a.py:g\njev_model: jev-2026-10\n", encoding="utf-8")
    bands = tmp_path / "calibration" / "bands.json"
    args = ["calibrate", str(DEMO_LABELS), "--config", str(config), "--bands", str(bands)]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "| judge | band | n | TPR: catches human-failed cases |" in out
    assert "| answer_states_order_status | flag | 40 | 0.90 [0.70, 0.97] |" in out
    assert "jev_model: jev-2026-10" in out
    doc = json.loads(bands.read_text(encoding="utf-8"))
    assert (doc["jev_model"], doc["judges"]["answer_states_order_status"]["band"]) == (
        "jev-2026-10",
        "flag",
    )


def test_cli_judge_respects_the_bands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    taxonomy = tmp_path / "taxonomy.yaml"
    taxonomy.write_text(UNDECIDED_TAXONOMY, encoding="utf-8")
    args = [*_judge_args(tmp_path), "--taxonomy", str(taxonomy), "--fail-on-inconclusive"]
    assert main(args) == 0  # uncalibrated: advisory, so even INCONCLUSIVE can't fail it
    assert "⚠️ 1 of 1 judges uncalibrated: no model judge can block this PR." in (
        capsys.readouterr().out
    )
    _bands(tmp_path / "bands.json", answer_states_order_status="flag")
    assert main(args) == 1  # a flag judge's INCONCLUSIVE counts
    assert "| answer_states_order_status (flag) |" in capsys.readouterr().out


# T102: an override on one PR moves the judge's band on the next, through git.
def _calibrated_project(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    _ci_project(project, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    shutil.copy(DEMO_TAXONOMY, project / "taxonomy.yaml")
    (project / "calibration" / "labels").mkdir(parents=True)
    shutil.copy(DEMO_LABELS, project / "calibration" / "labels" / "demo.csv")
    assert main(["calibrate", "--taxonomy", "taxonomy.yaml"]) == 0  # flag, from 40 labels


def _override(case_id: str, reviewer: str) -> int:
    return main(
        [
            *("review", "override", "--kpi", "answer_states_order_status", "--case-id", case_id),
            *("--judge-label", "pass", "--human-label", "fail", "--reviewer", reviewer),
            *("--note", "leaves out the order status", "--graph", "graph.py:graph"),
            *("--cassette-dir", "cassettes", "--taxonomy", "taxonomy.yaml"),
        ]
    )


def test_three_overrides_move_the_demo_band_and_the_next_ci_comment_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil

    project = tmp_path / "pr"
    project.mkdir()
    _calibrated_project(project, monkeypatch)
    shutil.copytree(project, tmp_path / "base")  # main, before the overrides
    # The demo has two runs: two reviewers on run-0, one on run-1, all three misses.
    for case_id, reviewer in [("run-0/respond", "ahmed"), ("run-1/respond", "ahmed")]:
        assert _override(case_id, reviewer) == 0
    capsys.readouterr()
    assert _override("run-0/respond", "sara") == 0
    # against the bands the last override left (n 42), then the PR's against main's (n 40)
    since_last = "Judge `answer_states_order_status`: flag → advisory (n 42 → 43, lower TPR 0.61"
    assert since_last in capsys.readouterr().out
    change = (
        "Judge `answer_states_order_status`: flag → advisory (n 40 → 43, lower TPR 0.70 → 0.58)."
    )
    bands = json.loads((project / "calibration" / "bands.json").read_text(encoding="utf-8"))
    assert bands["judges"]["answer_states_order_status"]["band"] == "advisory"
    cases = (project / "calibration" / "cases" / "answer_states_order_status.jsonl").read_text()
    saved = [json.loads(line) for line in cases.splitlines()]
    assert [c["case_id"] for c in saved] == ["run-1/respond", "run-0/respond"]  # one each
    overrides = (project / "calibration" / "overrides.jsonl").read_text(encoding="utf-8")
    assert '"reason": "leaves out the order status"' in overrides  # --note
    _, report, _ = _run_ci(project, "--base-dir", str(tmp_path / "base"))
    assert "### Judge calibration" in report and change in report


def test_cli_calibrate_with_no_label_files_leaves_the_bands_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["calibrate"]) == 0
    assert "No calibration labels" in capsys.readouterr().out
    assert not (tmp_path / "calibration" / "bands.json").exists()


def test_saved_cases_are_rejudged_and_a_drifting_judge_is_flagged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = tmp_path / "calibration" / "cases" / "answer_states_order_status.jsonl"
    cases.parent.mkdir(parents=True)
    saved = {"node_input": {"question": "hours?"}, "node_output": {"answer": "9-5"}}
    cases.write_text(
        "".join(
            json.dumps({**saved, "case_id": f"c{i}", "human_label": "fail"}) + "\n"
            for i in range(4)
        ),
        encoding="utf-8",
    )
    args = [*_judge_args(tmp_path), "--taxonomy", DEMO_TAXONOMY]
    args[args.index("--bands") + 1] = str(tmp_path / "calibration" / "bands.json")
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "Planned: 12 judge calls, 8 to model judges" in out  # k = 1, plus 4 saved cases
    assert (
        "`answer_states_order_status`: the judge agrees with the human on 0 of 4 saved cases."
        in out
    )
    assert "⚠️ `answer_states_order_status`: judge drifting from human labels" in out


# T101a: `lgcov bench` scores lgcov's root / propagated labels against hand-written ones.
BENCH = FIXTURES / "bench"


def _bench(tmp_path: Path, *args: str) -> tuple[int, dict[str, object], str]:
    md, results = tmp_path / "benchmark.md", tmp_path / "results.json"
    code = main(["bench", *args, "--out", str(md), "--json", str(results)])
    raw = json.loads(results.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return code, raw, md.read_text(encoding="utf-8")


def _rows(results: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = results["benchmarks"]
    assert isinstance(rows, list)
    return {str(r["name"]): r for r in rows}


@pytest.mark.slow
def test_cli_bench_scores_the_linear_fixture_perfectly(tmp_path: Path) -> None:
    code, results, md = _bench(tmp_path, str(BENCH), "--only", "linear")
    assert code == 0
    [row] = _rows(results).values()  # --only: just the one
    assert (row["root"], row["propagated"]) == (
        {"tp": 1, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0},
        {"tp": 1, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0},
    )
    assert "| linear | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 | `b` | `b` |" in md


@pytest.mark.slow
def test_cli_bench_a_wrong_expected_file_gives_the_fp_and_fn_you_would_predict(
    tmp_path: Path,
) -> None:
    import shutil

    bench = tmp_path / "bench"
    shutil.copytree(BENCH / "linear", bench / "linear")
    yaml_text = (BENCH / "linear.yaml").read_text(encoding="utf-8")
    wrong = yaml_text.replace("root: [b]", "root: [c]").replace(
        "propagated: [c]", "propagated: [b]"
    )
    (bench / "linear.yaml").write_text(wrong, encoding="utf-8")
    _, results, _ = _bench(tmp_path, str(bench))
    row = _rows(results)["linear"]
    for which in ("root", "propagated"):
        assert row[which] == {"tp": 0, "fp": 1, "fn": 1, "precision": 0.0, "recall": 0.0, "f1": 0.0}


@pytest.mark.slow
def test_cli_bench_reports_the_subgraph_miss_as_a_known_gap(tmp_path: Path) -> None:
    code, results, md = _bench(tmp_path, str(BENCH))
    assert code == 0  # a report, not a gate
    rows = _rows(results)
    assert sorted(rows) == ["linear", "multi_root", "send_fanout", "subgraph"]
    assert (rows["send_fanout"]["found_root"], rows["send_fanout"]["found_propagated"]) == (
        ["worker_b"],
        ["reducer"],
    )
    # Drift inside a subgraph: `sub` drifts first, but has no fingerprint, so T162 can't call it
    # root; `sub.inner` isn't recorded (T193).
    assert (rows["subgraph"]["first_drift"], rows["subgraph"]["expected_root"]) == (
        ["sub"],
        ["sub.inner"],
    )
    assert (rows["subgraph"]["found_root"], rows["subgraph"]["inconclusive"]) == (
        [],
        {"sub": "no fingerprint: a subgraph or the like (T193)"},
    )
    # T162: both nodes whose own code changed are root, not just the first to drift.
    assert (rows["multi_root"]["found_root"], rows["multi_root"]["found_propagated"]) == (
        ["b", "c"],
        ["d", "e"],
    )
    assert rows["multi_root"]["first_drift"] == ["b"]
    assert "❌ known gap (subgraph)" in md and "## Known gaps" in md


@pytest.mark.slow
def test_cli_bench_reads_base_and_head_from_git_refs(tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), *args],
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    (repo / "graph.py").write_text((BENCH / "linear" / "base.py").read_text(), encoding="utf-8")
    git("add", "graph.py")
    git("commit", "-q", "-m", "base")
    git("tag", "base")
    (repo / "graph.py").write_text((BENCH / "linear" / "head.py").read_text(), encoding="utf-8")
    git("commit", "-q", "-am", "head")
    yaml_text = (BENCH / "linear.yaml").read_text(encoding="utf-8")
    refs = yaml_text.replace(
        "base_patch: linear/base.py\nhead_patch: linear/head.py",
        "repo: repo\nbase_ref: base\nhead_ref: HEAD",
    )
    (tmp_path / "bench").mkdir()
    (tmp_path / "bench" / "linear.yaml").write_text(refs.replace("repo: repo", "repo: ../repo"))
    _, results, _ = _bench(tmp_path, str(tmp_path / "bench"))
    assert _rows(results)["linear"]["root"] == {
        "tp": 1,
        "fp": 0,
        "fn": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }


# T104: `lgcov lint`, the graph's shape before any replay: warnings only, unless --strict.
DEMO_AGENT = Path(__file__).resolve().parents[1] / "examples" / "demo_agent"
ORPHAN = """
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class S(TypedDict, total=False):
    x: int


def noop(state: S) -> S:
    return {}


g = StateGraph(S)
g.add_node("a", noop)
g.add_node("orphan", noop)
g.add_edge(START, "a")
g.add_edge("a", END)
g.add_edge("orphan", END)
graph = g.compile()
"""


def test_cli_lint_the_demo_agent_is_clean(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["lint", str(DEMO_AGENT), "--strict"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[-1] == "🧹 Lint: 0 warnings · 2 info"  # its KPIs all read respond
    assert "info `classify`: no KPI in the taxonomy names it." in out


def test_cli_lint_warns_and_exits_1_only_with_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)  # no lgcov.yaml and no cassettes: no never-hit check
    (tmp_path / "graph.py").write_text(ORPHAN, encoding="utf-8")
    assert main(["lint", "graph.py:graph"]) == 0
    out = capsys.readouterr().out
    assert "warn `orphan`: unreachable from START." in out
    assert "never-hit check skipped" in out
    assert out.splitlines()[-1] == "🧹 Lint: 1 warning"
    assert main(["lint", "graph.py:graph", "--strict"]) == 1


def test_cli_ci_shows_the_lint_line_and_never_blocks_on_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = DEMO_GRAPH.read_text(encoding="utf-8")
    orphan = 'b.add_node("orphan", respond)\nb.add_edge("orphan", END)\ngraph = b.compile()'
    _ci_project(tmp_path, monkeypatch, demo.replace("graph = b.compile()", orphan))
    code, report, _ = _run_ci(tmp_path)
    assert code == 0  # no drift: the orphan never runs
    assert "\n🧹 Lint: 1 warning\n" in report
    assert "- warn `orphan`: unreachable from START." in report


def test_cli_bench_lists_the_163_row_as_labels_reviewed(tmp_path: Path) -> None:
    # T100: docs/benchmark/, `lgcov bench`'s default folder; reviewed, but never run here.
    rows = Path(__file__).resolve().parents[1] / "docs" / "benchmark"
    code, results, md = _bench(tmp_path, str(rows))
    assert code == 0
    row = _rows(results)["open_deep_research-163"]
    assert (row["status"], row["error"], row["seconds"]) == ("labels_reviewed", "", 0)
    assert "| open_deep_research-163 | — | — | — |" in md and "📝 labels reviewed, not run |" in md


# T162: the #163 runs stage 1 kept, hashed so they compare as replay does without the model's text
# (tests/fixtures/bench_runs/README.md). Scoring them costs nothing.
SAVED_RUNS = FIXTURES / "bench_runs"


def test_cli_bench_from_runs_scores_the_saved_163_runs_without_recording(tmp_path: Path) -> None:
    rows = Path(__file__).resolve().parents[1] / "docs" / "benchmark"
    code, results, md = _bench(tmp_path, str(rows), "--from-runs", str(SAVED_RUNS))
    assert code == 0
    row = _rows(results)["open_deep_research-163"]
    assert (row["status"], row["error"]) == ("", "")
    # T162: first-drift attribution said root `write_research_brief` (1 of 6). Saved runs can't
    # be probed, and a probe of a model node would need a live call (T211), so the nodes whose
    # code changed are INCONCLUSIVE, not root. `research_supervisor` is a subgraph with no
    # fingerprint, and the other five roots are its inner nodes, which aren't recorded (T193).
    assert (row["found_root"], row["first_drift"]) == ([], ["write_research_brief"])
    assert row["inconclusive"] == {
        "final_report_generation": "not probed: scored from saved runs",
        "research_supervisor": "no fingerprint: a subgraph or the like (T193)",
        "write_research_brief": "not probed: scored from saved runs",
    }
    assert row["root"] == {"tp": 0, "fp": 0, "fn": 6, "precision": 0.0, "recall": 0.0, "f1": 0.0}
    assert "--from-runs" in md


def test_cli_bench_from_runs_a_missing_run_is_an_error_row(tmp_path: Path) -> None:
    rows = Path(__file__).resolve().parents[1] / "docs" / "benchmark"
    code, results, _ = _bench(tmp_path, str(rows), "--from-runs", str(tmp_path / "none"))
    assert code == 0
    row = _rows(results)["open_deep_research-163"]
    assert "base.json" in str(row["error"])


@pytest.mark.parametrize("other", [["--live"], ["--keep-runs", "kept"], ["--synthetic"]])
def test_cli_bench_from_runs_refuses_what_would_run_the_agent(
    tmp_path: Path, other: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["bench", "--from-runs", str(SAVED_RUNS), *other])
    assert code == 2
    assert "--from-runs" in capsys.readouterr().err


# T106: `lgcov taxonomy draft` proposes, `lgcov taxonomy approve` is the human's yes.
def _draft_args(tmp_path: Path) -> list[str]:
    from fixtures.drafting import TEAM

    (tmp_path / "team.md").write_text(TEAM, encoding="utf-8")
    traces = tmp_path / "traces"
    traces.mkdir()
    ok = {"nodes": ["classify", "lookup_order", "respond"], "tools": ["lookup_order"]}
    skipped = {
        "nodes": ["classify", "respond"],
        "failure": {"node": "respond", "kind": "skipped_lookup"},
    }
    rude = {**ok, "id": "t5", "failure": {"node": "respond", "kind": "rude_tone"}}
    runs = [{**ok, "id": "t1"}, {**ok, "id": "t2"}, {**skipped, "id": "t3"}]
    (traces / "a.json").write_text(json.dumps(runs), encoding="utf-8")  # a list of traces
    (traces / "b.json").write_text(json.dumps({**skipped, "id": "t4"}), encoding="utf-8")  # one
    (traces / "c.json").write_text(json.dumps(rude), encoding="utf-8")
    files = [str(traces / f) for f in ("a.json", "b.json", "c.json")]
    return ["--description", str(tmp_path / "team.md"), "--traces", *files]


def test_cli_taxonomy_draft_is_refused_by_check_until_a_human_approves_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from fixtures.drafting import Scripted

    monkeypatch.setattr("lgcov.cli._drafter", Scripted())
    out = tmp_path / "docs" / "taxonomy.draft.yaml"
    draft = ["taxonomy", "draft", *_draft_args(tmp_path), "--out", str(out)]
    assert main([*draft, "--system", "demo_agent"]) == 0
    assert "2 failure modes drafted, pending human review" in capsys.readouterr().out
    assert main(["taxonomy", "check", str(out)]) == 1
    err = capsys.readouterr().err
    assert "4 entries pending human review; approve with `lgcov taxonomy approve <id>`" in err
    for entry in ("skips_order_lookup", "rude_reply"):
        assert main(["taxonomy", "approve", entry, "--reviewer", "ahmed", "--file", str(out)]) == 0
    assert main(["taxonomy", "check", str(out)]) == 0
    assert "reviewer: ahmed" in out.read_text(encoding="utf-8")
    assert main([*draft]) == 0  # nothing new: approved entries stay as they are
    assert "No new failure signatures" in capsys.readouterr().out


CLIENT = """
from lgcov.draft import Proposal


class Client:
    def propose(self, description, group):
        kind = group.kind
        return Proposal(kind, f"{group.node}: {kind}", "seen", f"no_{kind}", "d", "r?")
"""


def test_cli_taxonomy_draft_takes_its_client_from_a_file(tmp_path: Path) -> None:
    (tmp_path / "client.py").write_text(CLIENT, encoding="utf-8")
    out = tmp_path / "draft.yaml"
    args = [
        *_draft_args(tmp_path),
        "--out",
        str(out),
        "--client",
        f"{tmp_path / 'client.py'}:Client",
    ]
    assert main(["taxonomy", "draft", *args]) == 0
    assert "id: skipped_lookup" in out.read_text(encoding="utf-8")


def test_cli_taxonomy_draft_loads_a_client_that_needs_its_module(tmp_path: Path) -> None:
    client = CLIENT.replace("class Client:", '@dataclass\nclass Client:\n    name: str = "x"\n')
    (tmp_path / "client.py").write_text(
        f"{_FUTURE}from dataclasses import dataclass\n{client}", encoding="utf-8"
    )
    args = [*_draft_args(tmp_path), "--out", str(tmp_path / "draft.yaml")]
    assert main(["taxonomy", "draft", *args, "--client", f"{tmp_path / 'client.py'}:Client"]) == 0


def test_cli_taxonomy_draft_needs_a_client(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["taxonomy", "draft", *_draft_args(tmp_path), "--out", str(tmp_path / "d.yaml")])
    assert code == 2
    assert "no drafting client" in capsys.readouterr().err


# T114: `lgcov ci` and `lgcov judge` lock the commits they compare from start to end.
MOVING_GRAPH = FIXTURES / "moving_graph.py"  # respond commits to $LGCOV_MOVE_REPO


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return done.stdout.decode().strip()


def _repo(path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Commit everything at path to a new repo, away from your own git config; its HEAD."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "lgcov test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")
    _git(path, "init", "-q")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "--allow-empty", "-m", "start")
    return _git(path, "rev-parse", "HEAD")


@pytest.mark.parametrize("side", [None, "base", "candidate"])
def test_cli_ci_locks_the_base_and_the_candidate_for_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, side: str | None
) -> None:
    _ci_project(tmp_path, monkeypatch, MOVING_GRAPH.read_text(encoding="utf-8"))
    (tmp_path / ".gitignore").write_text("base/\nout/\n", encoding="utf-8")
    (tmp_path / "base").mkdir()
    (tmp_path / "base" / "graph.py").write_text(DEMO_GRAPH.read_text(encoding="utf-8"))
    heads = {
        "candidate": _repo(tmp_path, monkeypatch),
        "base": _repo(tmp_path / "base", monkeypatch),
    }
    if side is not None:  # respond runs live (its code changed) and commits there each time
        monkeypatch.setenv(
            "LGCOV_MOVE_REPO", str(tmp_path / "base" if side == "base" else tmp_path)
        )
    code, report, outputs = _run_ci(tmp_path, "--base-dir", "base")
    if side is None:
        assert (code, outputs["verdict"]) == (0, "pass")
        base, candidate = heads["base"][:7], heads["candidate"][:7]
        assert f"🔒 Locked for this run: base `{base}`, candidate `{candidate}`.\n" in report
    else:
        assert (code, outputs["verdict"]) == (2, "error")
        assert f"the {side} was `{heads[side][:7]}` at the start and is `" in report
        assert "Coverage:" not in report and "🔒" not in report  # no verdict from mixed code


def test_cli_judge_refuses_when_the_candidate_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _judge_args(tmp_path)
    (tmp_path / "graph.py").write_text(MOVING_GRAPH.read_text(encoding="utf-8"))
    args[args.index("--graph") + 1] = f"{tmp_path / 'graph.py'}:graph"
    monkeypatch.chdir(tmp_path)
    head = _repo(tmp_path, monkeypatch)
    monkeypatch.setenv("LGCOV_MOVE_REPO", str(tmp_path))
    assert main([*args, "--taxonomy", DEMO_TAXONOMY]) == 2
    out, err = capsys.readouterr()
    assert f"🔒 Locked for this run: candidate `{head[:7]}`." in out
    assert (
        f"lgcov judge: Locked commits moved during the run: the candidate was `{head[:7]}`" in err
    )
    assert "| KPI |" not in out  # no gate table from two versions of the code


def test_cli_ci_warns_of_uncommitted_changes_the_lock_cannot_see(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    head = _repo(tmp_path, monkeypatch)
    (tmp_path / "notes.txt").write_text("not committed\n", encoding="utf-8")
    code, report, outputs = _run_ci(tmp_path)
    assert (code, outputs["verdict"]) == (0, "pass")  # a warning only, never a refusal
    warned = "⚠️ uncommitted changes, not covered by the lock"
    assert f"🔒 Locked for this run: candidate `{head[:7]}`. {warned}\n" in report


# T103: `lgcov import langsmith`, against a fake LangSmith (tests/fixtures/langsmith_api.py).
def _langsmith(monkeypatch: pytest.MonkeyPatch, fake: Fake, key: bool = True) -> None:
    monkeypatch.setattr("lgcov.cli._langsmith_client", fake)
    monkeypatch.delenv("LANGSMITH_ENDPOINT", raising=False)
    if key:
        monkeypatch.setenv("LANGSMITH_API_KEY", KEY)
    else:
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)


def test_cli_import_langsmith_writes_cases_and_a_sidecar_in_the_same_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = Fake({"/datasets": [Reply(200, [{"id": "d1"}])], "/examples": [Reply(200, EXAMPLES)]})
    _langsmith(monkeypatch, fake)
    out = tmp_path / "cases.json"
    assert main(["import", "langsmith", "--dataset", "support-qa", "--out", str(out)]) == 0
    assert json.loads(out.read_text("utf-8")) == [
        {"question": "Where is my order?"},
        {"question": "What are your hours?"},
    ]
    sidecar = json.loads((tmp_path / "cases.langsmith.json").read_text("utf-8"))
    assert sidecar == [
        {"example_id": "ex-1", "outputs": {"answer": "order #42: shipped"}},
        {"example_id": "ex-2", "outputs": None},
    ]
    printed = capsys.readouterr()
    assert f"Imported 2 cases from LangSmith dataset `support-qa` to {out}" in printed.out
    assert KEY not in printed.out + printed.err


def test_cli_import_langsmith_passes_limit_and_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Fake(
        {"/datasets": [Reply(200, [{"id": "d1"}])], "/examples": [Reply(200, EXAMPLES[:1])]}
    )
    _langsmith(monkeypatch, fake)
    args = ["import", "langsmith", "--dataset", "support-qa", "--out", str(tmp_path / "c.json")]
    assert main([*args, "--limit", "1", "--split", "test"]) == 0
    assert fake.calls[1][:2] == (
        "/examples",
        {"dataset": "d1", "splits": "test", "offset": 0, "limit": 1},
    )


@pytest.mark.parametrize(
    ("routes", "key", "message"),
    [
        ({}, False, "LANGSMITH_API_KEY isn't set; nothing imported"),
        ({"/datasets": [Reply(200, [])]}, True, "no LangSmith dataset named `nope`"),
    ],
)
def test_cli_import_langsmith_exits_2_and_writes_nothing_when_it_cannot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    routes: dict[str, list[Reply | Exception]],
    key: bool,
    message: str,
) -> None:
    _langsmith(monkeypatch, Fake(routes), key)
    out = tmp_path / "cases.json"
    assert main(["import", "langsmith", "--dataset", "nope", "--out", str(out)]) == 2
    assert message in capsys.readouterr().err
    assert not out.exists()


# T103 PR 2: `lgcov ci` links the experiment on lgcov.yaml's dataset that ran on this commit.
SESSION: dict[str, object] = {
    "id": "s1",
    "name": "exp-s1",
    "start_time": "2026-10-01T09:00:00",
    "tenant_id": "t1",
    "reference_dataset_id": "d1",
    "extra": {"metadata": {"git": {"commit": "c1"}}},
}
LINK = (
    "🔗 LangSmith experiment for this commit: "
    "[exp-s1](https://smith.langchain.com/o/t1/datasets/d1/compare?selectedSessions=s1)"
)


def _ci_langsmith(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake: Fake,
    section: str = "{dataset: support-qa}",
    key: bool = True,
    pr_head: str | None = None,
) -> tuple[int, str]:
    """`lgcov ci --commit c1` on the demo, with lgcov.yaml's langsmith section and a fake."""
    _ci_project(tmp_path, monkeypatch, DEMO_GRAPH.read_text(encoding="utf-8"))
    (tmp_path / "lgcov.yaml").write_text(f"langsmith: {section}\n", encoding="utf-8")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    if pr_head is not None:  # the pull_request event GitHub hands the job
        event = tmp_path / "event.json"
        event.write_text(json.dumps({"pull_request": {"head": {"sha": pr_head}}}), "utf-8")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    _langsmith(monkeypatch, fake, key)
    code, report, _ = _run_ci(tmp_path, "--commit", "c1")
    return code, report


def _found(*sessions: dict[str, object]) -> Fake:
    sessions_json = list(sessions)  # JSON arrays decode as lists
    return Fake(
        {"/datasets": [Reply(200, [{"id": "d1"}])], "/sessions": [Reply(200, sessions_json)]}
    )


def test_cli_ci_links_the_experiment_run_on_the_commit_it_tested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, report = _ci_langsmith(tmp_path, monkeypatch, _found(SESSION))
    assert code == 0 and LINK in report


def test_cli_ci_links_the_experiment_run_on_the_prs_head_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_head = {**SESSION, "extra": {"metadata": {"git": {"commit": "c9"}}}}
    code, report = _ci_langsmith(tmp_path, monkeypatch, _found(on_head), pr_head="c9")
    assert code == 0 and LINK in report


def test_cli_ci_says_nothing_when_no_experiment_ran_on_this_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = {**SESSION, "extra": {"metadata": {"git": {"commit": "c2"}}}}
    code, report = _ci_langsmith(tmp_path, monkeypatch, _found(elsewhere))
    assert code == 0 and "LangSmith" not in report


@pytest.mark.parametrize(
    ("routes", "key", "note"),
    [
        (
            {},
            False,
            "LangSmith: LANGSMITH_API_KEY isn't set (fork PRs get no secrets); no experiment "
            "link for `support-qa`.",
        ),
        (
            {"/datasets": [Reply(503), Reply(503)]},
            True,
            "⚠️ LangSmith: /datasets unreachable after 2 attempts (HTTP 503); no experiment link.",
        ),
    ],
)
def test_cli_ci_notes_a_missing_key_or_a_langsmith_outage_and_exits_as_before(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, list[Reply | Exception]],
    key: bool,
    note: str,
) -> None:
    code, report = _ci_langsmith(tmp_path, monkeypatch, Fake(routes), key=key)
    assert code == 0 and note in report


def test_cli_ci_makes_no_langsmith_call_with_link_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Fake({})  # any request would fail the test
    section = "{dataset: support-qa, link: false}"
    code, report = _ci_langsmith(tmp_path, monkeypatch, fake, section=section, key=False)
    assert (code, fake.calls) == (0, []) and "LangSmith" not in report


def test_bench_synthetic_writes_its_section_and_keeps_the_rest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    md, results = tmp_path / "benchmark.md", tmp_path / "results.json"
    md.write_text("# B\n\n## Real PRs\n\nby hand\n", encoding="utf-8")
    code = main(["bench", "--synthetic", "--out", str(md), "--json", str(results)])
    assert code == 0
    doc = md.read_text(encoding="utf-8")
    assert doc.index("## Synthetic") < doc.index("## Real PRs") and "by hand" in doc
    raw = json.loads(results.read_text(encoding="utf-8"))
    assert raw["cases"] >= 50
    assert raw["all_edits"][0]["method"] == "lgcov root"
    assert "## Synthetic" in capsys.readouterr().out


def test_bench_synthetic_scores_lgcovs_real_root_not_the_first_divergence(tmp_path: Path) -> None:
    results = tmp_path / "results.json"
    main(["bench", "--synthetic", "--out", str(tmp_path / "b.md"), "--json", str(results)])
    raw = json.loads(results.read_text(encoding="utf-8"))
    roots = {row["method"]: row["cases"] for row in raw["all_edits"]}
    assert roots["lgcov root"]["recall"]["of"] == roots["first divergence"]["recall"]["of"] == 31


@pytest.mark.parametrize("extra", [["--only", "x"], ["--keep-runs", "x"], ["--live"], ["some/dir"]])
def test_bench_synthetic_refuses_what_only_the_fixture_bench_takes(
    extra: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["bench", "--synthetic", *extra]) == 2
    assert "--synthetic" in capsys.readouterr().err


def test_bench_synthetic_outside_a_checkout_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("lgcov.cli._synthetic_agents", Mock(side_effect=ModuleNotFoundError("x")))
    assert main(["bench", "--synthetic", "--out", str(tmp_path / "b.md")]) == 2
    assert "repo checkout" in capsys.readouterr().err


def test_bench_without_synthetic_keeps_its_default_results_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["bench", str(BENCH), "--only", "linear", "--out", "b.md"]) == 0
    assert (tmp_path / "docs" / "benchmark-results.json").exists()


def test_bench_names_a_changed_router_s_source_node_root(tmp_path: Path) -> None:
    # T175: scored from saved runs, as `lgcov bench --from-runs` does. No node's code changed.
    from lgcov.attribution import parse_benchmark
    from lgcov.cassette import Cassette, Step, runs_to_json
    from lgcov.cli import _bench_saved

    raw = {"graph": "g.py:graph", "base_patch": "b.py", "head_patch": "h.py", "cases": [{}]}
    expected = {"root": ["classify"], "propagated": ["respond"]}
    bench = parse_benchmark("routed", {**raw, "expected": expected}, tmp_path)
    (tmp_path / "routed").mkdir()
    for name, router, answer in (("base.json", "r1", "a"), ("head.json", "r2", "b")):
        steps = [Step("classify", 1, {}, "c1", router=router), Step("respond", answer, {}, "c2")]
        (tmp_path / "routed" / name).write_text(runs_to_json([Cassette({}, steps)]), "utf-8")
    row = _bench_saved(bench, tmp_path)
    assert (row.error, row.inconclusive) == ("", ())
    assert (row.found_root, row.found_propagated) == (("classify",), ("respond",))


def _real_suite() -> list[Case]:
    return [case for agent in AGENTS for case in every_case(agent)]


def test_on_single_node_edits_the_first_divergence_ties_lgcovs_root_on_every_score() -> None:
    cases = [c for c in _real_suite() if c.truth.edit_kind == "node_body"]
    assert rates([votes_for(_lgcov_root, c) for c in cases]) == rates(
        [votes_for(METHODS["first divergence"], c) for c in cases]
    )


def test_on_a_conditional_edge_edit_lgcovs_root_is_the_edge_s_source_node() -> None:
    # T175: `classify` for a router edit, `plan` for a dropped Send. Its output didn't change, so
    # the first divergence is a node downstream and never the edited one.
    edge = [c for c in _real_suite() if c.truth.edit_kind == "conditional_edge"]
    changed = [c for c in edge if c.truth.activated]
    assert {c.operator for c in changed} == {"router_flip", "send_fan_out_drop"}
    assert all(_lgcov_root(c) == {c.truth.edited_node} for c in changed)
    assert not any(c.truth.edited_node in METHODS["first divergence"](c) for c in changed)
    assert all(_lgcov_root(c) == set() for c in edge if not c.truth.activated)


def test_on_the_real_suite_the_diff_matches_or_beats_lgcov_on_recall_and_coverage() -> None:
    single = [c for c in _real_suite() if c.truth.edit_kind == "node_body"]
    ours = rates([votes_for(_lgcov_root, c) for c in single])
    diff = rates([votes_for(METHODS["edited node from the diff"], c) for c in single])
    for mine, theirs in ((ours.recall, diff.recall), (ours.coverage, diff.coverage)):
        assert mine.value is not None and theirs.value is not None and mine.value <= theirs.value


def test_every_single_node_miss_lgcov_makes_is_parent_only_naming_of_a_subgraph() -> None:
    misses = [
        (c.truth.edited_node, _lgcov_root(c))
        for c in _real_suite()
        if c.truth.edit_kind == "node_body"
        and c.truth.activated
        and c.truth.edited_node not in _lgcov_root(c)
    ]
    assert misses and all(found == ("sub.inner", {"sub"}) for found in misses)


def test_docs_benchmark_md_holds_the_current_synthetic_section(tmp_path: Path) -> None:
    fresh = tmp_path / "b.md"
    assert main(["bench", "--synthetic", "--out", str(fresh), "--json", str(tmp_path / "r")]) == 0
    doc = (Path(__file__).resolve().parents[1] / "docs" / "benchmark.md").read_text("utf-8")
    undated = re.compile(r"^Run \d{4}-\d\d-\d\d:", re.MULTILINE)
    assert undated.sub("Run:", fresh.read_text("utf-8")).strip() in undated.sub("Run:", doc)


def test_a_jev_judge_takes_its_model_and_timeout_from_lgcov_yaml() -> None:
    # T195: jev_timeout_s reaches the judge `lgcov judge` and `lgcov ci` build
    spec = next(
        s
        for s in load_taxonomy(
            str(Path(__file__).resolve().parents[1] / "docs" / "taxonomy.yaml")
        ).kpi_specs
        if s.judge == "jev"
    )
    judge = _l2_judge(spec, "gpt-6-luna", 120.0)
    assert isinstance(judge, JevJudge)
    assert (judge.model, judge.timeout_s) == ("gpt-6-luna", 120.0)
    default = _l2_judge(spec)
    assert isinstance(default, JevJudge) and default.timeout_s == 10.0
