"""T165: lgcov's own commands on a graph whose state holds LangChain messages."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from lgcov.cassette import Step
from lgcov.cli import _l2_cases, _load_runs, _write_runs, main
from lgcov.jev_judge import JevJudge
from lgcov.taxonomy import KPISpec

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


def test_lgcov_cassette_accept_finds_no_drift_from_new_ids(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _record(tmp_path)
    assert main(["cassette", "accept", "--all-drifted", "--config", str(cfg)]) == 1
    assert "no drifted nodes" in capsys.readouterr().err


def test_lgcov_cassette_accept_uses_the_config_s_ignore_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _record(tmp_path)
    with cfg.open("a", encoding="utf-8") as f:
        f.write('ignore_paths: ["/messages/*/tool_calls/*/args/q"]\n')
    (tmp_path / "graph.py").write_text(
        GRAPH.replace('QUERY = "x"', 'QUERY = "y"'), encoding="utf-8"
    )
    assert main(["cassette", "accept", "--all-drifted", "--config", str(cfg)]) == 1
    assert "no drifted nodes" in capsys.readouterr().err


def test_lgcov_cassette_accept_re_baselines_a_changed_message_node(tmp_path: Path) -> None:
    cfg = _record(tmp_path)
    (tmp_path / "graph.py").write_text(
        GRAPH.replace('QUERY = "x"', 'QUERY = "y"'), encoding="utf-8"
    )
    assert main(["replay", "--config", str(cfg)]) == 1
    assert main(["cassette", "accept", "--nodes", "model", "--config", str(cfg)]) == 0
    assert main(["replay", "--config", str(cfg)]) == 0


class _Jev:
    """A JEV stand-in that sends each request through json.dumps, as httpx does."""

    status_code = 200

    def __init__(self, kpi: str) -> None:
        self.kpi = kpi
        self.sent: dict[str, Any] = {}

    def post(
        self, url: str, *, json: dict[str, Any], timeout: float, headers: dict[str, str]
    ) -> "_Jev":
        self.sent = _through_json(json)
        return self

    def json(self) -> dict[str, Any]:
        return {"answers": {self.kpi: {"choice": "pass", "confidence": 0.9}}}


def _through_json(value: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(json.dumps(value))
    return body


def test_a_model_judge_is_sent_plain_json_not_message_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JEV_API_URL", "http://jev.test")
    monkeypatch.setenv("JEV_API_KEY", "not-a-real-key")
    spec = KPISpec("k", "f", "n", "jev", "a description", "a rubric")
    step = Step(
        "n",
        {"messages": [AIMessage("out", id="run-1")]},
        {"messages": [HumanMessage("in", id="u1")]},
        "code",
    )
    client = _Jev("k")
    node_input, node_output = _l2_cases(spec, [[step]])["run-0/n"]
    JevJudge(spec.to_kpi(), client=client).judge(node_input, node_output)
    assert client.sent["state"] == {
        "input": {"messages": [{"type": "human", "content": "in"}]},
        "output": {"messages": [{"type": "ai", "content": "out"}]},
    }


def test_lgcov_review_override_saves_messages_as_plain_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "graph.py").write_text(GRAPH, encoding="utf-8")
    (tmp_path / "inputs.json").write_text(INPUTS, encoding="utf-8")
    (tmp_path / "lgcov.yaml").write_text(
        "graph_entrypoint: graph.py:graph\ncassette_dir: cassettes\n", encoding="utf-8"
    )
    assert main(["record", "--inputs", "inputs.json"]) == 0
    shutil.copy(ROOT / "tests" / "fixtures" / "demo_taxonomy.yaml", "taxonomy.yaml")
    (tmp_path / "calibration" / "labels").mkdir(parents=True)
    shutil.copy(ROOT / "calibration" / "labels" / "demo.csv", "calibration/labels/demo.csv")
    assert main(["calibrate", "--taxonomy", "taxonomy.yaml"]) == 0
    assert (
        main(
            [
                *("review", "override", "--kpi", "answer_states_order_status"),
                *("--case-id", "run-0/respond", "--judge-label", "pass", "--human-label", "fail"),
                *("--reviewer", "ahmed", "--note", "wrong", "--graph", "graph.py:graph"),
                *("--cassette-dir", "cassettes", "--taxonomy", "taxonomy.yaml"),
            ]
        )
        == 0
    )
    said = {"messages": [{"type": "ai", "content": "final: found"}]}
    overrides = (tmp_path / "calibration" / "overrides.jsonl").read_text(encoding="utf-8")
    assert json.loads(overrides.splitlines()[-1])["output"] == said
    cases = tmp_path / "calibration" / "cases" / "answer_states_order_status.jsonl"
    assert json.loads(cases.read_text(encoding="utf-8").splitlines()[-1])["node_output"] == said


# Serving can't fit a step's ids: it runs live, and every command says so.
AMBIGUOUS = """\
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph

CALLS = Path("calls.log")  # by name: `lgcov bench` runs each side from its own folder
PROMPT = "v1"
ORDER = 1


def _called(name: str) -> None:
    with CALLS.open("a", encoding="utf-8") as f:
        f.write(name + "\\n")


def model(state: MessagesState) -> MessagesState:
    _called(f"model {PROMPT}")
    calls = [
        {"name": "search", "args": {"q": "x"}, "id": f"call_{uuid4().hex}"} for _ in range(2)
    ]
    return {"messages": [AIMessage("", id=f"run-{uuid4()}", tool_calls=calls)]}


def tools(state: MessagesState) -> MessagesState:
    _called("tools")
    calls = state["messages"][-1].tool_calls[::ORDER]
    results = [ToolMessage("found", tool_call_id=c["id"], name=c["name"]) for c in calls]
    return {"messages": results}


def respond(state: MessagesState) -> MessagesState:
    _called("respond")
    first = state["messages"][-2].tool_call_id
    return {"messages": [ToolMessage("late", tool_call_id=first, name="search")]}


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
AMBIGUOUS_CHANGED = AMBIGUOUS.replace('PROMPT = "v1"', 'PROMPT = "v2"').replace(
    "ORDER = 1", "ORDER = -1"
)
SKIPPED = "rebind_skipped: respond (run 1)"


def _ambiguous_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Record, then change the graph so that `respond`'s ids can't be fitted."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "graph.py").write_text(AMBIGUOUS, encoding="utf-8")
    (tmp_path / "inputs.json").write_text(INPUTS, encoding="utf-8")
    (tmp_path / "lgcov.yaml").write_text(
        "graph_entrypoint: graph.py:graph\ncassette_dir: cassettes\n", encoding="utf-8"
    )
    assert main(["record", "--inputs", "inputs.json"]) == 0
    (tmp_path / "graph.py").write_text(AMBIGUOUS_CHANGED, encoding="utf-8")
    _calls(tmp_path)


def test_lgcov_replay_says_rebind_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _ambiguous_project(tmp_path, monkeypatch)
    capsys.readouterr()
    assert main(["replay", "--summary-json", "summary.json"]) == 0  # it ran live, and matched
    assert SKIPPED in capsys.readouterr().out
    assert _calls(tmp_path) == ["model v2", "tools", "respond"]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["rebind_skipped"] == ["respond"]


def test_lgcov_replay_says_nothing_when_nothing_was_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _record(tmp_path)
    capsys.readouterr()
    summary = tmp_path / "summary.json"
    assert main(["replay", "--config", str(cfg), "--summary-json", str(summary)]) == 0
    assert "rebind_skipped" not in capsys.readouterr().out
    assert "rebind_skipped" not in json.loads(summary.read_text(encoding="utf-8"))


def test_lgcov_ci_puts_rebind_skipped_in_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ambiguous_project(tmp_path, monkeypatch)
    assert main(["ci", "--report", "report.md"]) == 0
    assert SKIPPED in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_lgcov_review_says_rebind_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _ambiguous_project(tmp_path, monkeypatch)
    shutil.copy(ROOT / "tests" / "fixtures" / "demo_taxonomy.yaml", "taxonomy.yaml")
    (tmp_path / "calibration" / "labels").mkdir(parents=True)
    shutil.copy(ROOT / "calibration" / "labels" / "demo.csv", "calibration/labels/demo.csv")
    assert main(["calibrate", "--taxonomy", "taxonomy.yaml"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                *("review", "override", "--kpi", "answer_states_order_status"),
                *("--case-id", "run-0/respond", "--judge-label", "pass", "--human-label", "fail"),
                *("--reviewer", "ahmed", "--note", "wrong", "--graph", "graph.py:graph"),
                *("--cassette-dir", "cassettes", "--taxonomy", "taxonomy.yaml"),
            ]
        )
        == 0
    )
    assert SKIPPED in capsys.readouterr().err


def test_lgcov_judge_says_rebind_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _ambiguous_project(tmp_path, monkeypatch)
    shutil.copy(ROOT / "tests" / "fixtures" / "demo_taxonomy.yaml", "taxonomy.yaml")
    capsys.readouterr()
    main(
        [
            *("judge", "--graph", "graph.py:graph", "--cassette-dir", "cassettes"),
            *("--taxonomy", "taxonomy.yaml", "--bands", "calibration/bands.json"),
        ]
    )
    assert SKIPPED in capsys.readouterr().err


@pytest.mark.slow
def test_lgcov_bench_says_rebind_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The paid benchmark row goes through here: a skipped serve there is a real call.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "base.py").write_text(AMBIGUOUS, encoding="utf-8")
    (tmp_path / "head.py").write_text(AMBIGUOUS_CHANGED, encoding="utf-8")
    (tmp_path / "ambiguous.yaml").write_text(
        "graph: graph.py:graph\nbase_patch: base.py\nhead_patch: head.py\n"
        "cases:\n  - {messages: [{role: user, content: hi}]}\n"
        "expected:\n  root: []\n  propagated: []\n"
        "notes: Its tools answer in the other order.\nhard_cases: []\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    main(["bench", str(tmp_path), "--out", "bench.md", "--json", "bench.json"])
    assert SKIPPED in capsys.readouterr().err
    rows = json.loads((tmp_path / "bench.json").read_text(encoding="utf-8"))
    assert "error" not in rows["benchmarks"][0] or not rows["benchmarks"][0]["error"]
