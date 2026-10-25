import json
from pathlib import Path
from typing import Any


def record_trace(graph: Any, inputs: dict[str, Any]) -> list[str]:
    """Run a compiled LangGraph graph and return visited node names in order.

    graph is typed Any on purpose: LangGraph's generic types change between
    versions and would break mypy --strict on upgrades. A Protocol can replace
    it later.
    """
    visited: list[str] = []
    for chunk in graph.stream(inputs, stream_mode="updates"):
        visited.extend(chunk.keys())
    return visited


def save_traces(traces: list[list[str]], path: Path) -> None:
    path.write_text(json.dumps({"runs": traces}, indent=2) + "\n")
