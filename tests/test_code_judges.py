"""T072: deterministic trajectory judges read from recorded traces."""

import pytest
from fixtures.graphs import linear

from lgcov.code_judges import (
    MaxSteps,
    NodeNotVisited,
    NodeVisited,
    ToolCalled,
    ToolNotCalled,
    code_judge,
)
from lgcov.judges import KPI, JudgeInfraError, NodeState
from lgcov.trace import record_trace

KPI_ = KPI.binary("k", "one line", "trajectory check")


def _out(nodes: list[str], tools: list[str] | None = None) -> NodeState:
    run: dict[str, list[str]] = {"nodes": nodes}
    if tools is not None:
        run["tools"] = tools
    return {"trace": run}


def test_tool_not_called_fails_when_trace_contains_tool() -> None:
    v = ToolNotCalled(KPI_, "web_search").judge({}, _out(["a"], ["think", "web_search"]))
    assert v.label == "fail"
    assert v.score == 0.0
    assert v.confidence == 1.0
    assert v.raw["reason"] == "tool web_search called at step 2"


def test_tool_not_called_passes_without_tools_recorded() -> None:
    assert ToolNotCalled(KPI_, "web_search").judge({}, _out(["a"])).label == "pass"


def test_tool_called_passes_when_present_fails_when_absent() -> None:
    judge = ToolCalled(KPI_, "web_search")
    assert judge.judge({}, _out(["a"], ["web_search"])).label == "pass"
    absent = judge.judge({}, _out(["a"], ["think"]))
    assert absent.label == "fail"
    assert absent.raw["reason"] == "tool web_search never called"


def test_node_visited_and_not_visited_on_linear_fixture() -> None:
    out = {"trace": record_trace(linear(), {"x": 1})}  # legacy format: ["a", "b"]
    assert NodeVisited(KPI_, "b").judge({}, out).label == "pass"
    assert NodeVisited(KPI_, "c").judge({}, out).label == "fail"
    assert NodeNotVisited(KPI_, "c").judge({}, out).label == "pass"
    v = NodeNotVisited(KPI_, "a").judge({}, out)
    assert v.label == "fail"
    assert v.raw["reason"] == "node a visited at step 1"


def test_max_steps_passes_at_n_fails_at_n_plus_one() -> None:
    judge = MaxSteps(KPI_, 3)
    assert judge.judge({}, _out(["a", "b", "c"])).label == "pass"
    over = judge.judge({}, _out(["a", "b", "c", "d"]))
    assert over.label == "fail"
    assert over.raw["reason"] == "4 steps, max 3"


@pytest.mark.parametrize("out", [{}, {"trace": 42}, {"trace": {"tools": ["x"]}}])
def test_missing_or_malformed_trace_is_infra_error(out: NodeState) -> None:
    with pytest.raises(JudgeInfraError):
        ToolNotCalled(KPI_, "web_search").judge({}, out)


def test_code_judge_builds_from_check() -> None:
    assert code_judge(KPI_, {"type": "tool_not_called", "tool": "s"}) == ToolNotCalled(KPI_, "s")
    assert code_judge(KPI_, {"type": "max_steps", "n": "5"}) == MaxSteps(KPI_, 5)


def test_code_judge_unknown_check_lists_valid_checks() -> None:
    with pytest.raises(ValueError, match=r"oracle.*max_steps.*tool_not_called"):
        code_judge(KPI_, {"type": "oracle"})


@pytest.mark.parametrize("check", [{"type": "tool_called"}, {"type": "max_steps", "n": "lots"}, {}])
def test_code_judge_bad_params_raise_value_error(check: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        code_judge(KPI_, check)


def test_code_judge_needs_binary_kpi() -> None:
    kpi = KPI("k", "d", ("good", "bad"), "r", {"good": 1.0, "bad": 0.0})
    with pytest.raises(ValueError, match="pass/fail"):
        code_judge(kpi, {"type": "tool_called", "tool": "s"})
