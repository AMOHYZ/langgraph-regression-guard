"""T152 fixture 2: plan -> worker -> check, three times round. The PR changes one task."""

import operator
from collections.abc import Callable
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph


class State(TypedDict, total=False):
    visits: int
    task: str
    history: Annotated[list[str], operator.add]


class WorkerInput(TypedDict, total=False):
    task: str


def build(
    tasks: tuple[str, ...], spy: Callable[[str], object], narrow: bool = False
) -> CompiledStateGraph[State]:
    """`narrow`: the worker reads only its task, so a later visit can match the recording again.
    Otherwise it reads the history too, so one changed visit changes every later input."""

    def plan(state: State) -> State:
        visits = state.get("visits", 0)
        return {"visits": visits + 1, "task": tasks[visits]}

    def worker(state: WorkerInput) -> State:
        spy(state["task"])
        return {"history": [f"done {state['task']}"]}

    def check(state: State) -> State:
        return {}

    def route(state: State) -> str:
        return "plan" if state["visits"] < len(tasks) else END

    g = StateGraph(State)
    g.add_node("plan", plan)
    g.add_node("worker", worker, input_schema=WorkerInput if narrow else State)
    g.add_node("check", check)
    g.add_edge(START, "plan")
    g.add_edge("plan", "worker")
    g.add_edge("worker", "check")
    g.add_conditional_edges("check", route, ["plan", END])
    return g.compile()
