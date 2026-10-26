import json
from pathlib import Path

import pytest
from fixtures.graphs import conditional, fan_out_send, linear

from lgcov.trace import RunTrace, load_traces, parse_run, record_trace, save_traces


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


def test_parse_run_accepts_legacy_list_and_optional_tools() -> None:
    assert parse_run(["a", "b"]) == RunTrace(("a", "b"))
    assert parse_run({"nodes": ["a"], "tools": ["web_search"]}) == RunTrace(("a",), ("web_search",))
    assert parse_run({"nodes": ["a"]}).tools == ()


@pytest.mark.parametrize("raw", [42, {"tools": ["x"]}, {"nodes": [1]}, ["a", 2]])
def test_parse_run_rejects_malformed(raw: object) -> None:
    with pytest.raises(ValueError):
        parse_run(raw)


def test_load_traces_reads_save_traces_output(tmp_path: Path) -> None:
    path = tmp_path / "traces.json"
    save_traces([["a", "b"]], path)
    assert load_traces(path) == [RunTrace(("a", "b"))]
