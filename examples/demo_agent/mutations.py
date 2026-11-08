"""T121: the edits the synthetic generator makes to the demo agent, by operator class in
docs/mutations.yaml. That file's entries name open_deep_research's nodes, so only the classes
carry over. Each edit changes the agent's code, and the fake LLM keeps the re-run offline."""

from collections.abc import Callable
from random import Random
from types import FunctionType
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from examples.demo_agent.graph import State, classify, lookup_order, respond, route
from lgcov.cassette import Cassette
from lgcov.synthetic import Mutation

# Classes in docs/mutations.yaml this agent has nothing to apply to.
NOT_ON_DEMO = {
    "send_fan_out_drop": "the demo agent has no fan-out",
    "subgraph_output_change": "the demo agent has no subgraph",
}

# The prompt cassettes/baseline.json was recorded with. The demo PR edits graph.py's copy, and
# the edits here must start from the recording, or they would carry that PR's change too.
RECORDED_PROMPT = "You are a concise support agent."


def outcome_ok(run: Cassette) -> bool:
    """The end-to-end outcome, read from the question and not from `needs_tool`, so a broken
    classify can't pass its own check: an answer, stating the order's status when, and only
    when, the question asks about an order. Not the taxonomy's answer_states_order_status:
    that judge is seeded to fail every 3rd judgment."""
    answers = [step.output["answer"] for step in run.steps if step.node == "respond"]
    asked = "order" in run.inputs["question"].lower()
    return bool(answers) and ("shipped" in answers[-1]) == asked


def build(
    lookup: Callable[..., Any] = lookup_order,
    answer: Callable[..., Any] | None = None,
    router: Callable[..., Any] = route,
    schema: Any = State,
) -> Any:
    """The demo agent with parts swapped, wired as in graph.py, which a test checks. Any:
    LangGraph's generic types change between versions, as in lgcov.cassette.record."""
    b = StateGraph(schema)
    b.add_node("classify", classify)
    b.add_node("lookup_order", lookup)
    b.add_node("respond", answer or _respond_with())
    b.add_edge(START, "classify")
    b.add_conditional_edges("classify", router)
    b.add_edge("lookup_order", "respond")
    b.add_edge("respond", END)
    return b.compile()


class _Renamed(State, total=False):
    order_status: str


def _key_renamed(state: State) -> _Renamed:
    return {"order_status": lookup_order(state)["tool_result"]}


def _returning(tool_result: str) -> Callable[[State], State]:
    def lookup(state: State) -> State:
        return {"tool_result": tool_result}

    return lookup


def _respond_with(**names: object) -> FunctionType:
    """respond as recorded, with these module-level names changed as a PR would change them."""
    names = {"SYSTEM_PROMPT": RECORDED_PROMPT, **names}
    return FunctionType(respond.__code__, {**respond.__globals__, **names}, respond.__name__)


def _model(window: int) -> Callable[[str], str]:
    def fake_llm(prompt: str) -> str:
        return f"[{len(prompt)}] " + prompt[-window:]

    return fake_llm


def _flipped(state: State) -> Literal["lookup_order", "respond"]:
    return "respond" if state["needs_tool"] else "lookup_order"


def _key_written_twice(state: State) -> _Renamed:
    result = lookup_order(state)["tool_result"]
    return {"tool_result": result, "order_status": result}


def _always_lookup(state: State) -> Literal["lookup_order", "respond"]:
    return "lookup_order"


def _never_lookup(state: State) -> Literal["lookup_order", "respond"]:
    return "respond"


MUTATIONS: dict[str, tuple[Mutation, ...]] = {
    "state_key_rename": (
        Mutation(
            "lookup_order",
            "tool_result becomes order_status, and respond still reads the old key",
            build(lookup=_key_renamed, schema=_Renamed),
        ),
    ),
    "retrieval_k_change": (
        Mutation("lookup_order", "the tool's results go from 1 to 0", build(_returning(""))),
    ),
    "tool_schema_change": (
        Mutation(
            "lookup_order",
            "the result becomes JSON, with the status near the end",
            build(_returning('{"order": 42, "status": "shipped"}')),
        ),
        Mutation(
            "lookup_order",
            "the result becomes JSON, with the status too far back for the model to see",
            build(_returning('{"status": "shipped", "order": 42, "carrier": "post", "eta": "2d"}')),
        ),
    ),
    "model_swap": tuple(
        Mutation(
            "respond",
            f"the model keeps the prompt's last {window} characters, not 40",
            build(answer=_respond_with(fake_llm=_model(window))),
        )
        for window in (12, 30, 60)
    ),
    "prompt_edit": (
        Mutation(
            "respond",
            "the prompt becomes friendly and detailed",
            build(
                answer=_respond_with(SYSTEM_PROMPT="You are a friendly, detailed support agent.")
            ),
        ),
        Mutation(
            "respond",
            "the prompt is reworded at the same length, which the fake LLM's answer doesn't show",
            build(answer=_respond_with(SYSTEM_PROMPT="Be a very concise support agent.")),
        ),
    ),
    # route is classify's conditional edge, not a node: the edit sits at classify, in no output.
    "router_flip": (
        Mutation("classify", "route is inverted", build(router=_flipped), "conditional_edge"),
    ),
}

# T121: the suite's other edits on this agent. MUTATIONS stays as the 20 cases tests pin.
MORE_MUTATIONS: dict[str, tuple[Mutation, ...]] = {
    "state_key_rename": (
        Mutation(
            "lookup_order",
            "order_status is written next to tool_result, which respond still reads",
            build(lookup=_key_written_twice, schema=_Renamed),
        ),
    ),
    "retrieval_k_change": (
        Mutation(
            "lookup_order",
            "the tool's results go from 1 to 2, with this order's first",
            build(_returning("order #42: shipped; order #43: pending")),
        ),
        Mutation(
            "lookup_order",
            "the tool's results go from 1 to 2, with this order's last",
            build(_returning("order #43: pending; order #42: shipped")),
        ),
    ),
    "prompt_edit": (
        Mutation(
            "respond",
            "the prompt ends on a word the outcome check looks for",
            build(answer=_respond_with(SYSTEM_PROMPT="Say when an item has shipped.")),
        ),
    ),
    "router_flip": (
        Mutation(
            "classify",
            "route always goes to the tool",
            build(router=_always_lookup),
            "conditional_edge",
        ),
        Mutation(
            "classify",
            "route never goes to the tool",
            build(router=_never_lookup),
            "conditional_edge",
        ),
    ),
}

NOISE_RATE = 0.2  # ours, for the A/A wiring test: it says nothing about a real model


def noisy_graph(seed: int) -> Any:
    """The unedited agent, except that its fake model sometimes keeps too little of the prompt."""
    window = 12 if Random(seed).random() < NOISE_RATE else 40
    return build(answer=_respond_with(fake_llm=_model(window)))
