"""T168: lgcov's own commands on a graph whose nodes return Command."""

from pathlib import Path

import pytest

from lgcov.cli import main

GRAPH = """\
from pathlib import Path
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

CALLS = Path(__file__).with_name("calls.log")
ROUTE = "b"


class S(TypedDict, total=False):
    x: int
    y: str


def _called(name: str) -> None:
    with CALLS.open("a", encoding="utf-8") as f:
        f.write(name + "\\n")


async def a(state: S) -> Command[Literal["b", "c"]]:
    _called("a")
    return Command(goto=ROUTE, update={"x": 2})


def b(state: S) -> S:
    _called("b")
    return {"y": "same"}


def c(state: S) -> S:
    _called("c")
    return {"y": "same"}


g = StateGraph(S)
g.add_node("a", a)
g.add_node("b", b)
g.add_node("c", c)
g.add_edge(START, "a")
g.add_edge("b", END)
g.add_edge("c", END)
graph = g.compile()
"""


def _project(tmp_path: Path) -> Path:
    (tmp_path / "graph.py").write_text(GRAPH, encoding="utf-8")
    (tmp_path / "inputs.json").write_text('[{"x": 1}]', encoding="utf-8")
    cfg = tmp_path / "lgcov.yaml"
    cfg.write_text(
        f"graph_entrypoint: {tmp_path / 'graph.py'}:graph\n"
        f"cassette_dir: {tmp_path / 'cassettes'}\n",
        encoding="utf-8",
    )
    assert main(["record", "--config", str(cfg), "--inputs", str(tmp_path / "inputs.json")]) == 0
    return cfg


def _calls(tmp_path: Path) -> list[str]:
    """The real calls since the last time this was asked."""
    log = tmp_path / "calls.log"
    calls = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
    log.unlink(missing_ok=True)
    return calls


def _send_to_c(tmp_path: Path) -> None:
    (tmp_path / "graph.py").write_text(
        GRAPH.replace('ROUTE = "b"', 'ROUTE = "c"'), encoding="utf-8"
    )


def test_lgcov_replay_serves_a_command_node_with_no_real_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _project(tmp_path)
    assert _calls(tmp_path) == ["a", "b"]
    assert '"goto": [\n' in (tmp_path / "cassettes" / "baseline.json").read_text(encoding="utf-8")
    capsys.readouterr()
    assert main(["replay", "--config", str(cfg)]) == 0
    assert _calls(tmp_path) == []
    assert "No cassette drift" in capsys.readouterr().out


def test_lgcov_replay_reports_a_goto_change_as_root_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _project(tmp_path)
    _send_to_c(tmp_path)
    _calls(tmp_path)
    capsys.readouterr()
    assert main(["replay", "--config", str(cfg), "--fail-on", "root-drift"]) == 1
    assert "| `a` | root | 1/1 |" in capsys.readouterr().out
    assert _calls(tmp_path) == ["a", "c"]


def test_lgcov_ci_reports_a_goto_change_as_root_drift(tmp_path: Path) -> None:
    cfg = _project(tmp_path)
    _send_to_c(tmp_path)
    report = tmp_path / "report.md"
    args = ["ci", "--config", str(cfg), "--report", str(report), "--fail-on", "root-drift"]
    assert main(args) == 1
    assert "| `a` | root | 1/1 |" in report.read_text(encoding="utf-8")


def test_lgcov_cassette_accept_finds_and_re_baselines_a_goto_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _project(tmp_path)
    assert main(["cassette", "accept", "--all-drifted", "--config", str(cfg)]) == 1  # no change
    _send_to_c(tmp_path)
    capsys.readouterr()
    assert main(["cassette", "accept", "--nodes", "a", "--config", str(cfg)]) == 0
    row = next(r for r in capsys.readouterr().out.splitlines() if r.startswith("| `a` |"))
    old, new = row.split("|")[2:4]
    assert old != new  # the update didn't change, but where it sent the run did
