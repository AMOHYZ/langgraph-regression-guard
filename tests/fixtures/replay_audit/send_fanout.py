"""T152 fixture 3: plan fans out to one worker per task, then collect. The PR changes the tasks."""

import operator
from collections.abc import Callable
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send


class State(TypedDict, total=False):
    tasks: list[dict[str, str]]
    results: Annotated[list[str], operator.add]
    summary: str


class Task(TypedDict, total=False):
    # What a task's payload calls its name. Of these, comparing masks `id`; replay's key masks none.
    task: str
    task_id: str
    id: str


def no_spy(name: str) -> None:
    pass


def build(
    names: tuple[str, ...], spy: Callable[[str], object] = no_spy, key: str = "task"
) -> CompiledStateGraph[State]:
    def plan(state: State) -> State:
        return {"tasks": [{key: name} for name in names]}

    def fan_out(state: State) -> list[Send]:
        return [Send("worker", task) for task in state["tasks"]]

    def worker(state: Task) -> State:
        name = state.get("task") or state.get("task_id") or state.get("id") or ""
        spy(name)
        return {"results": [f"result for {name}"]}

    def collect(state: State) -> State:
        return {"summary": ", ".join(sorted(state["results"]))}

    g = StateGraph(State)
    g.add_node("plan", plan)
    g.add_node("worker", worker)
    g.add_node("collect", collect)
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", fan_out, ["worker"])
    g.add_edge("worker", "collect")
    g.add_edge("collect", END)
    return g.compile()
