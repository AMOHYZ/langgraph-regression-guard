"""T079: the demo agent example: 3 nodes, 1 tool, 1 conditional edge, a deterministic fake LLM."""

import importlib.util
from pathlib import Path
from types import ModuleType

from lgcov.snapshot import Edge, snapshot_from_compiled
from lgcov.trace import record_trace

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_agent" / "graph.py"


def _demo() -> ModuleType:
    spec = importlib.util.spec_from_file_location("demo_agent_graph", DEMO)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_snapshot_has_three_nodes_and_one_conditional_branch_point() -> None:
    snap = snapshot_from_compiled(_demo().graph)
    assert snap.nodes == {"__start__", "classify", "lookup_order", "respond", "__end__"}
    assert {e for e in snap.edges if e.conditional} == {
        Edge("classify", "lookup_order", True),
        Edge("classify", "respond", True),
    }


def test_order_question_uses_the_tool() -> None:
    trace = record_trace(_demo().graph, {"question": "Where is my order?"})
    assert trace == ["classify", "lookup_order", "respond"]


def test_other_question_skips_the_tool() -> None:
    assert record_trace(_demo().graph, {"question": "Hi there"}) == ["classify", "respond"]


def test_fake_llm_is_deterministic() -> None:
    graph = _demo().graph
    first = graph.invoke({"question": "Where is my order?"})["answer"]
    assert first == graph.invoke({"question": "Where is my order?"})["answer"]
    assert first.startswith("[")
