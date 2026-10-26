import json
from dataclasses import dataclass
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


@dataclass(frozen=True)
class RunTrace:
    """One recorded run: visited nodes in order, plus tool calls in order if recorded."""

    nodes: tuple[str, ...]
    tools: tuple[str, ...] = ()


def _names(value: object, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"trace: '{key}' must be a list of strings")
    return tuple(value)


def parse_run(raw: object) -> RunTrace:
    """A run as saved: a legacy list of node names, or {"nodes": [...], "tools": [...]}."""
    if isinstance(raw, list):
        return RunTrace(_names(raw, "nodes"))
    if not isinstance(raw, dict) or "nodes" not in raw:
        raise ValueError("trace: a run must be a list of nodes or a mapping with 'nodes'")
    return RunTrace(_names(raw["nodes"], "nodes"), _names(raw.get("tools", []), "tools"))


def load_traces(path: Path) -> list[RunTrace]:
    runs = json.loads(path.read_text()).get("runs")
    if not isinstance(runs, list):
        raise ValueError(f"{path}: 'runs' must be a list")
    return [parse_run(r) for r in runs]
