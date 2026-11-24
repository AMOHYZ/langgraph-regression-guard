"""T210: tool_called / tool_not_called only see the KPI's named node's tool calls."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from lgcov.cassette import Step
from lgcov.cli import _l2_cases
from lgcov.code_judges import code_judge
from lgcov.taxonomy import KPISpec


def _ai(tool: str, call_id: str) -> AIMessage:
    return AIMessage("", id=f"ai-{call_id}", tool_calls=[{"name": tool, "args": {}, "id": call_id}])


def test_supervisor_think_tool_does_not_satisfy_researcher_uses_think_tool() -> None:
    """False-pass today (whole-run): supervisor's think_tool must not pass node:researcher."""
    steps = [
        Step("supervisor", {"messages": [_ai("think_tool", "s1"), _ai("ConductResearch", "s2")]}),
        Step("researcher", {"messages": [_ai("web_search", "r1")]}),
    ]
    check: dict[str, object] = {"type": "tool_called", "tool": "think_tool"}
    spec = KPISpec(
        "researcher_uses_think_tool",
        "researcher_skips_think_tool",
        "researcher",
        "code",
        "The researcher reflects with think_tool.",
        "Did the researcher call think_tool at least once?",
        check=check,
    )
    cases = _l2_cases(spec, [steps])
    assert "run-0/researcher" in cases
    _, node_out = cases["run-0/researcher"]
    # Researcher's own tools only — supervisor's think_tool must not appear.
    assert node_out["trace"]["tools"] == ["web_search"]
    assert "think_tool" not in node_out["trace"]["tools"]
    verdict = code_judge(spec.to_kpi(), check).judge({}, node_out)
    assert verdict.label == "fail"


def test_tool_called_on_named_node_passes_when_that_node_called_it() -> None:
    steps = [
        Step("supervisor", {"messages": [_ai("ConductResearch", "s1")]}),
        Step("researcher", {"messages": [_ai("think_tool", "r1")]}),
    ]
    check: dict[str, object] = {"type": "tool_called", "tool": "think_tool"}
    spec = KPISpec("k", "f", "researcher", "code", "d", "r", check=check)
    _, node_out = _l2_cases(spec, [steps])["run-0/researcher"]
    assert node_out["trace"]["tools"] == ["think_tool"]
    assert code_judge(spec.to_kpi(), check).judge({}, node_out).label == "pass"


def test_named_node_missing_from_run_is_unmeasured_not_pass() -> None:
    """Subgraph / unrecorded node: no case → never a silent pass from another node's tools."""
    steps = [
        Step("supervisor", {"messages": [_ai("think_tool", "s1")]}),
        # researcher ran inside a subgraph and was never recorded at top level
    ]
    check: dict[str, object] = {"type": "tool_called", "tool": "think_tool"}
    spec = KPISpec("k", "f", "researcher", "code", "d", "r", check=check)
    assert _l2_cases(spec, [steps]) == {}


def test_tool_not_called_ignores_other_nodes() -> None:
    steps = [
        Step("supervisor", {"messages": [_ai("refund_order", "s1")]}),
        Step("lookup_order", {"messages": [_ai("lookup_order", "l1")]}),
    ]
    check: dict[str, object] = {"type": "tool_not_called", "tool": "refund_order"}
    spec = KPISpec("k", "f", "lookup_order", "code", "d", "r", check=check)
    _, node_out = _l2_cases(spec, [steps])["run-0/lookup_order"]
    assert node_out["trace"]["tools"] == ["lookup_order"]
    assert code_judge(spec.to_kpi(), check).judge({}, node_out).label == "pass"
