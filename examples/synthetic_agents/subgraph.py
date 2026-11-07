"""T121: a subgraph for docs/mutations.yaml's subgraph_output_change, which the demo agent can't
hold. Shaped like tests/fixtures/bench/subgraph, with no model, so its runs are deterministic."""

from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from lgcov.cassette import Cassette
from lgcov.synthetic import Mutation

STYLE = "long"


class State(TypedDict, total=False):
    question: str
    draft: str
    answer: str


def before(state: State) -> State:
    return {"question": state["question"].strip()}


def inner(state: State) -> State:
    return {"draft": f"{STYLE}:{state['question']}"}


def after(state: State) -> State:
    return {"answer": state["draft"].upper()}


def build(
    inner_node: Callable[..., Any] = inner,
    after_node: Callable[..., Any] = after,
) -> Any:
    """The agent with parts swapped. Any: as in examples/demo_agent/mutations.py."""
    sub = StateGraph(State)
    sub.add_node("inner", inner_node)
    sub.add_edge(START, "inner")
    sub.add_edge("inner", END)
    b = StateGraph(State)
    b.add_node("before", before)
    b.add_node("sub", sub.compile())
    b.add_node("after", after_node)
    b.add_edge(START, "before")
    b.add_edge("before", "sub")
    b.add_edge("sub", "after")
    b.add_edge("after", END)
    return b.compile()


graph = build()


def outcome_ok(run: Cassette) -> bool:
    """The answer still holds the whole question."""
    answers = [step.output["answer"] for step in run.steps if step.node == "after"]
    return bool(answers) and run.inputs["question"].upper() in answers[-1]


def _cut(state: State) -> State:
    return {"draft": f"{STYLE}:{state['question']}"[:8]}


def _short(state: State) -> State:
    return {"draft": f"short:{state['question']}"}


def _same_draft(state: State) -> State:
    return {"draft": ":".join((STYLE, state["question"]))}


def _last_8(state: State) -> State:
    return {"answer": state["draft"].upper()[-8:]}


MUTATIONS: dict[str, tuple[Mutation, ...]] = {
    "subgraph_output_change": (
        Mutation("sub.inner", "the subgraph's draft is cut to 8 characters", build(_cut)),
        Mutation("sub.inner", "the subgraph's style becomes short", build(_short)),
        Mutation("sub.inner", "the same draft, built differently", build(_same_draft)),
    ),
    "model_swap": (
        Mutation(
            "after",
            "the model keeps the draft's last 8 characters",
            build(after_node=_last_8),
        ),
    ),
}
