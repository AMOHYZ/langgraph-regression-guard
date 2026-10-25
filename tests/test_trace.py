import json
from pathlib import Path

from fixtures.graphs import conditional, fan_out_send, linear

from lgcov.trace import record_trace, save_traces


def test_linear_trace() -> None:
    assert record_trace(linear(), {"x": 1}) == ["a", "b"]


def test_conditional_takes_tools_path_then_answers() -> None:
    assert record_trace(conditional(), {"route": "tools"}) == [
        "agent",
        "tools",
        "agent",
        "answer",
    ]


def test_send_fans_out_three_workers() -> None:
    trace = record_trace(fan_out_send(), {"x": 0})
    assert trace[0] == "plan"
    assert trace.count("worker") == 3


def test_save_traces_writes_runs_json(tmp_path: Path) -> None:
    path = tmp_path / "traces.json"
    save_traces([["a", "b"], ["agent"]], path)
    assert json.loads(path.read_text()) == {"runs": [["a", "b"], ["agent"]]}
