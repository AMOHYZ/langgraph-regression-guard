"""T165: lgcov's own commands on a graph whose state holds LangChain messages."""

from pathlib import Path

import pytest

from lgcov.cli import _load_runs, _write_runs, main

ROOT = Path(__file__).resolve().parents[1]
GRAPH = """\
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph

CALLS = Path(__file__).with_name("calls.log")
QUERY = "x"


def _called(name: str) -> None:
    with CALLS.open("a", encoding="utf-8") as f:
        f.write(name + "\\n")


async def model(state: MessagesState) -> MessagesState:
    _called("model")
    call = {"name": "search", "args": {"q": QUERY}, "id": f"call_{uuid4().hex}"}
    return {"messages": [AIMessage("", id=f"run-{uuid4()}", tool_calls=[call])]}


def tools(state: MessagesState) -> MessagesState:
    _called("tools")
    call = state["messages"][-1].tool_calls[0]
    return {"messages": [ToolMessage("found", tool_call_id=call["id"], name=call["name"])]}


async def respond(state: MessagesState) -> MessagesState:
    _called("respond")
    text = f"final: {state['messages'][-1].content}"
    return {"messages": [AIMessage(text, id=f"run-{uuid4()}")]}


g = StateGraph(MessagesState)
g.add_node("model", model)
g.add_node("tools", tools)
g.add_node("respond", respond)
g.add_edge(START, "model")
g.add_edge("model", "tools")
g.add_edge("tools", "respond")
g.add_edge("respond", END)
graph = g.compile()
"""
INPUTS = '[{"messages": [{"role": "user", "content": "hi"}]}]'


def _project(tmp_path: Path) -> Path:
    (tmp_path / "graph.py").write_text(GRAPH, encoding="utf-8")
    (tmp_path / "inputs.json").write_text(INPUTS, encoding="utf-8")
    cfg = tmp_path / "lgcov.yaml"
    cfg.write_text(
        f"graph_entrypoint: {tmp_path / 'graph.py'}:graph\n"
        f"cassette_dir: {tmp_path / 'cassettes'}\n",
        encoding="utf-8",
    )
    return cfg


def _record(tmp_path: Path) -> Path:
    cfg = _project(tmp_path)
    assert main(["record", "--config", str(cfg), "--inputs", str(tmp_path / "inputs.json")]) == 0
    return cfg


def _calls(tmp_path: Path) -> list[str]:
    """The real calls since the last time this was asked."""
    log = tmp_path / "calls.log"
    calls = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
    log.unlink(missing_ok=True)
    return calls


def test_lgcov_record_saves_a_run_that_holds_messages(tmp_path: Path) -> None:
    _record(tmp_path)
    assert _calls(tmp_path) == ["model", "tools", "respond"]
    saved = (tmp_path / "cassettes" / "baseline.json").read_text(encoding="utf-8")
    assert '"langchain_message"' in saved
    assert '"tool_call_id": "call_' in saved  # whole: a served tool result still names its call


def test_lgcov_replay_serves_a_saved_run_with_no_real_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _record(tmp_path)
    _calls(tmp_path)
    assert main(["replay", "--config", str(cfg)]) == 0
    assert _calls(tmp_path) == []
    assert "No cassette drift" in capsys.readouterr().out


def test_lgcov_replay_live_sees_no_drift_from_new_ids(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _record(tmp_path)
    _calls(tmp_path)
    assert main(["replay", "--config", str(cfg), "--live"]) == 0
    assert _calls(tmp_path) == ["model", "tools", "respond"]
    assert "No cassette drift" in capsys.readouterr().out


def test_lgcov_replay_reports_changed_tool_args_as_root_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _record(tmp_path)
    (tmp_path / "graph.py").write_text(
        GRAPH.replace('QUERY = "x"', 'QUERY = "y"'), encoding="utf-8"
    )
    capsys.readouterr()
    assert main(["replay", "--config", str(cfg), "--fail-on", "root-drift"]) == 1
    out = capsys.readouterr().out
    assert "| `model` | root | 1/1 |" in out
    assert "Unchanged: `respond`, `tools`" in out


@pytest.mark.parametrize(
    "baseline",
    [
        "examples/demo_agent/cassettes/baseline.json",
        "tests/fixtures/demo_baseline.json",
        "tests/fixtures/selfcheck/cassettes/baseline.json",
    ],
)
def test_a_cassette_from_before_messages_re_saves_byte_identical(
    tmp_path: Path, baseline: str
) -> None:
    out = tmp_path / "baseline.json"
    _write_runs(out, _load_runs(str(ROOT / baseline)))
    assert out.read_text(encoding="utf-8") == (ROOT / baseline).read_text(encoding="utf-8")
