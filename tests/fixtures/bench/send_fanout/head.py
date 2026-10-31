"""T101a fixture: a router fans out to three workers, then a reducer; the PR edits worker_b."""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

TONE = "cheerful"  # the PR's change
WORKERS = ("worker_a", "worker_b", "worker_c")


class State(TypedDict, total=False):
    question: str
    parts: Annotated[list[str], operator.add]
    summary: str


def plan(state: State) -> State:
    return {}


def fan_out(state: State) -> list[Send]:
    return [Send(name, state) for name in WORKERS]


def worker_a(state: State) -> State:
    return {"parts": [f"a:{state['question']}"]}


def worker_b(state: State) -> State:
    return {"parts": [f"b:{TONE}:{state['question']}"]}


def worker_c(state: State) -> State:
    return {"parts": [f"c:{state['question']}"]}


def reducer(state: State) -> State:
    return {"summary": " | ".join(sorted(state["parts"]))}


builder = StateGraph(State)
builder.add_node("plan", plan)
builder.add_node("worker_a", worker_a)
builder.add_node("worker_b", worker_b)
builder.add_node("worker_c", worker_c)
builder.add_node("reducer", reducer)
builder.add_edge(START, "plan")
builder.add_conditional_edges("plan", fan_out, list(WORKERS))
for name in WORKERS:
    builder.add_edge(name, "reducer")
builder.add_edge("reducer", END)
graph = builder.compile()
