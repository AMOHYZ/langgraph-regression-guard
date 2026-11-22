"""T133 case 4: plan emits fixed tasks; fan_out remaps Send payloads via REMAP."""

from __future__ import annotations

import operator
from collections.abc import Callable
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

# Remap applied inside fan_out; empty = identity. Fingerprinted with the router.
REMAP: dict[str, str] = {}


class State(TypedDict, total=False):
    tasks: list[dict[str, str]]
    results: Annotated[list[str], operator.add]
    summary: str


def no_spy(name: str) -> None:
    pass


def plan(state: State) -> State:
    return {"tasks": [{"task": t} for t in ("a", "b", "c")]}


def fan_out(state: State) -> list[Send]:
    return [Send("worker", {"task": REMAP.get(t["task"], t["task"])}) for t in state["tasks"]]


def collect(state: State) -> State:
    return {"summary": ", ".join(sorted(state.get("results", [])))}


def build(spy: Callable[[str], object] = no_spy) -> CompiledStateGraph[State]:
    def worker(state: dict[str, str]) -> State:
        name = state.get("task", "")
        spy(name)
        return {"results": [f"r {name}"]}

    g = StateGraph(State)
    g.add_node("plan", plan)
    g.add_node("worker", worker)
    g.add_node("collect", collect)
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", fan_out, ["worker"])
    g.add_edge("worker", "collect")
    g.add_edge("collect", END)
    return g.compile()
