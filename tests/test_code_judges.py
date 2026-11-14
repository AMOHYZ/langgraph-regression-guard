"""T072: deterministic trajectory judges read from recorded traces."""

import pytest
from fixtures.graphs import linear

from lgcov.code_judges import (
    MaxSteps,
    NodeNotVisited,
    NodeSequence,
    NodeVisited,
    ToolCalled,
    ToolNotCalled,
    ToolSequence,
    code_judge,
    unknown_names,
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


# T197: the names appear in this order; other steps may come between.
def test_node_sequence_passes_in_order_with_gaps_and_fails_out_of_order() -> None:
    judge = code_judge(KPI_, {"type": "node_sequence", "nodes": "classify, respond"})
    assert judge == NodeSequence(KPI_, ("classify", "respond"))
    ok = judge.judge({}, _out(["classify", "lookup_order", "respond"]))
    assert (ok.label, ok.confidence) == ("pass", 1.0)
    assert ok.raw["reason"] == "nodes classify, respond visited in order"
    late = judge.judge({}, _out(["respond", "classify"]))
    assert late.label == "fail"
    assert late.raw["reason"] == "node respond not visited after classify (step 2)"
    never = judge.judge({}, _out(["lookup_order", "respond"]))
    assert (never.label, never.raw["reason"]) == ("fail", "node classify never visited")


def test_a_name_listed_twice_must_be_seen_twice() -> None:
    judge = NodeSequence(KPI_, ("retry", "retry"))
    assert judge.judge({}, _out(["retry", "plan", "retry"])).label == "pass"
    once = judge.judge({}, _out(["retry", "plan"]))
    assert once.raw["reason"] == "node retry not visited after retry (step 1)"


def test_tool_sequence_reads_the_runs_tools() -> None:
    judge = code_judge(KPI_, {"type": "tool_sequence", "tools": "search,fetch"})
    assert judge == ToolSequence(KPI_, ("search", "fetch"))
    assert judge.judge({}, _out(["a"], ["search", "calc", "fetch"])).label == "pass"
    out = judge.judge({}, _out(["a"], ["fetch", "search"]))
    assert (out.label, out.raw["reason"]) == ("fail", "tool fetch not called after search (step 2)")
    with pytest.raises(JudgeInfraError):
        judge.judge({}, {})


@pytest.mark.parametrize("names", ["", " , ", "a,,b"])
def test_a_sequence_needs_names(names: str) -> None:
    with pytest.raises(ValueError):
        code_judge(KPI_, {"type": "node_sequence", "nodes": names})


def test_unknown_names_are_those_neither_side_has() -> None:
    check = {"type": "node_sequence", "nodes": "classify, lookup_ordr, inner"}
    assert unknown_names(check, {"classify", "lookup_order"}, {"classify"}) == (
        "lookup_ordr",
        "inner",
    )
    # on the base and gone from the PR is known: that is a failure to judge, not a typo
    assert unknown_names({"type": "tool_sequence", "tools": "search"}, {"search"}, set()) == ()
    assert unknown_names({"type": "node_visited", "node": "nope"}, set(), set()) == ()


def test_code_judge_needs_binary_kpi() -> None:
    kpi = KPI("k", "d", ("good", "bad"), "r", {"good": 1.0, "bad": 0.0})
    with pytest.raises(ValueError, match="pass/fail"):
        code_judge(kpi, {"type": "tool_called", "tool": "s"})
