"""T121: a fan-out for docs/mutations.yaml's send_fan_out_drop, which the demo agent can't hold.
Shaped like tests/fixtures/bench/send_fanout, with no model, so its runs are deterministic."""

import operator
from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from lgcov.cassette import Cassette
from lgcov.synthetic import Mutation

TONE = "plain"
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


def build(
    router: Callable[..., Any] = fan_out,
    b_worker: Callable[..., Any] = worker_b,
) -> Any:
    """The agent with parts swapped. Any: as in examples/demo_agent/mutations.py."""
    b = StateGraph(State)
    b.add_node("plan", plan)
    b.add_node("worker_a", worker_a)
    b.add_node("worker_b", b_worker)
    b.add_node("worker_c", worker_c)
    b.add_node("reducer", reducer)
    b.add_edge(START, "plan")
    b.add_conditional_edges("plan", router, list(WORKERS))
    for name in WORKERS:
        b.add_edge(name, "reducer")
    b.add_edge("reducer", END)
    return b.compile()


graph = build()


def outcome_ok(run: Cassette) -> bool:
    """The summary holds every worker's part."""
    summaries = [step.output["summary"] for step in run.steps if step.node == "reducer"]
    return bool(summaries) and all(f"{w}:" in summaries[-1] for w in "abc")


def _first_two(state: State) -> list[Send]:
    return [Send(name, state) for name in WORKERS[:2]]


def _two_on_orders(state: State) -> list[Send]:
    return _first_two(state) if "order" in state["question"].lower() else fan_out(state)


def _cheerful(state: State) -> State:
    return {"parts": [f"b:cheerful:{state['question']}"]}


def _no_results(state: State) -> State:
    return {"parts": []}


# fan_out is plan's conditional edge, so these edits sit at plan, in no output.
MUTATIONS: dict[str, tuple[Mutation, ...]] = {
    "send_fan_out_drop": (
        Mutation("plan", "only the first 2 workers run", build(_first_two), "conditional_edge"),
        Mutation(
            "plan",
            "only the first 2 workers run on an order question",
            build(_two_on_orders),
            "conditional_edge",
        ),
    ),
    "prompt_edit": (
        Mutation("worker_b", "worker_b's tone becomes cheerful", build(b_worker=_cheerful)),
    ),
    "retrieval_k_change": (
        Mutation("worker_b", "worker_b's results go from 1 to 0", build(b_worker=_no_results)),
    ),
}
