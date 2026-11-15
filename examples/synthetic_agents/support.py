"""T182: edits the diff can't pin to one node, on the demo's support flow. A helper, the model
name and a config default are each read by 2+ nodes. Two more builds: respond inside a subgraph,
the shape T220 found in open_deep_research, and the model name inside a client object built at
import. A fake model, so every run is deterministic and offline."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, make_dataclass
from types import FunctionType
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from lgcov.cassette import Cassette
from lgcov.synthetic import SharedEdit

MODEL = "fake-40"  # what classify and respond pass the fake model: keep a prompt's last 40 chars
SYSTEM_PROMPT = "You are a concise support agent."


@dataclass(frozen=True)
class Settings:
    max_chars: int = 60  # the longest text lookup_order and respond pass on


class State(TypedDict, total=False):
    question: str
    needs_tool: bool
    tool_result: str
    answer: str


def clean(text: str) -> str:  # the helper classify and respond share
    return text.strip().lower()


def fake_llm(model: str, prompt: str) -> str:
    return f"[{len(prompt)}] " + prompt[-int(model.removeprefix("fake-")) :]


class FakeChat:
    """A chat client built at import, as `ChatOpenAI(model=...)` is. A plain class, so its repr
    doesn't show the model."""

    def __init__(self, model: str) -> None:
        self.model = model

    def invoke(self, prompt: str) -> str:
        return fake_llm(self.model, prompt)


CLIENT = FakeChat(model="fake-40")


def classify(state: State) -> State:
    return {"needs_tool": "order" in fake_llm(MODEL, clean(state["question"]))}


def lookup_order(state: State) -> State:
    return {"tool_result": "order #42: shipped"[: Settings().max_chars]}


def respond(state: State) -> State:
    prompt = f"{SYSTEM_PROMPT}\n{state.get('tool_result', '')}\n"
    return {"answer": fake_llm(MODEL, prompt + clean(state["question"])[: Settings().max_chars])}


def classify_by_client(state: State) -> State:
    return {"needs_tool": "order" in CLIENT.invoke(clean(state["question"]))}


def respond_by_client(state: State) -> State:
    prompt = f"{SYSTEM_PROMPT}\n{state.get('tool_result', '')}\n"
    return {"answer": CLIENT.invoke(prompt + clean(state["question"])[: Settings().max_chars])}


def route(state: State) -> Literal["lookup", "answer"]:
    return "lookup" if state["needs_tool"] else "answer"


NODES: dict[str, Callable[..., Any]] = {
    "classify": classify,
    "lookup_order": lookup_order,
    "respond": respond,
}
BY_CLIENT = {"classify": classify_by_client, "respond": respond_by_client}


def build(nodes: Mapping[str, Callable[..., Any]] | None = None, nested: bool = False) -> Any:
    """The agent with nodes swapped in by name. `nested`: respond runs inside a one-node
    subgraph, `answer`. Any: as in examples/demo_agent/mutations.py."""
    fns = {**NODES, **(nodes or {})}
    answer: Any = fns["respond"]
    name = "respond"
    if nested:
        sub = StateGraph(State)
        sub.add_node("respond", fns["respond"])
        sub.add_edge(START, "respond")
        sub.add_edge("respond", END)
        answer, name = sub.compile(), "answer"
    b = StateGraph(State)
    b.add_node("classify", fns["classify"])
    b.add_node("lookup_order", fns["lookup_order"])
    b.add_node(name, answer)
    b.add_edge(START, "classify")
    b.add_conditional_edges("classify", route, {"lookup": "lookup_order", "answer": name})
    b.add_edge("lookup_order", name)
    b.add_edge(name, END)
    return b.compile()


graph, nested_graph, client_graph = build(), build(nested=True), build(BY_CLIENT)


def outcome_ok(run: Cassette) -> bool:
    """The demo's check: an answer, stating the order's status when, and only when, the question
    asks about an order. The answer is respond's, or the subgraph's that holds respond."""
    outputs = [step.output for step in run.steps if isinstance(step.output, dict)]
    answers = [output["answer"] for output in outputs if "answer" in output]
    asked = "order" in run.inputs["question"].lower()
    return bool(answers) and ("shipped" in answers[-1]) == asked


def _with(fn: Callable[..., Any], **names: object) -> FunctionType:
    """fn as it is, with these module-level names changed, as a PR would change them."""
    if not isinstance(fn, FunctionType):
        raise TypeError(f"{fn!r} is not a plain function")
    return FunctionType(fn.__code__, {**fn.__globals__, **names}, fn.__name__)


def _edit(
    variant: str,
    readers: tuple[str, ...],
    nested: bool = False,
    swapped: Mapping[str, Callable[..., Any]] | None = None,
    **names: object,
) -> SharedEdit:
    """The readers get `names` in their globals. `swapped`: the build's own nodes, by name. A
    touched node is named as the bench names it: a subgraph's inner node is <node>.<inner>."""
    nodes = {**NODES, **(swapped or {})}
    edited: dict[str, Callable[..., Any]] = {r: _with(nodes[r], **names) for r in readers}
    label = {r: "answer.respond" if nested and r == "respond" else r for r in readers}
    return SharedEdit(
        frozenset(label.values()),
        variant,
        build({**nodes, **edited}, nested),
        {label[r]: build({**nodes, r: fn}, nested) for r, fn in edited.items()},
    )


def _keeps_case(text: str) -> str:
    return text.strip()


def _asks(text: str) -> str:
    return "customer asks: " + text.strip().lower()


def _squeezed(text: str) -> str:
    return " ".join(text.split()).lower()


def _settings(max_chars: int) -> type:
    """Settings as a PR would leave it, with another default for max_chars."""
    return make_dataclass("Settings", [("max_chars", int, field(default=max_chars))], frozen=True)


SHARED = ("classify", "respond")  # what clean, MODEL and CLIENT reach
LIMITED = ("lookup_order", "respond")  # what Settings reaches
KEEPS_CASE, ASKS = "clean keeps the question's case", 'clean puts "customer asks: " first'

EDITS: dict[str, tuple[SharedEdit, ...]] = {
    "shared_helper": (
        _edit(KEEPS_CASE, SHARED, clean=_keeps_case),
        _edit(ASKS, SHARED, clean=_asks),
        _edit("clean also squeezes repeated spaces", SHARED, clean=_squeezed),
    ),
    "shared_model_swap": (
        _edit("the model keeps 5 characters, not 40", SHARED, MODEL="fake-5"),
        _edit("the model keeps 80 characters, not 40", SHARED, MODEL="fake-80"),
    ),
    "shared_config": (
        _edit("max_chars' default goes from 60 to 12", LIMITED, Settings=_settings(12)),
        _edit("max_chars' default goes from 60 to 30", LIMITED, Settings=_settings(30)),
    ),
}
NESTED_EDITS: dict[str, tuple[SharedEdit, ...]] = {
    "shared_helper": (
        _edit(KEEPS_CASE, SHARED, nested=True, clean=_keeps_case),
        _edit(ASKS, SHARED, nested=True, clean=_asks),
    ),
}
CLIENT_EDITS: dict[str, tuple[SharedEdit, ...]] = {
    "shared_model_swap": (
        _edit(
            "the client is built with fake-5", SHARED, swapped=BY_CLIENT, CLIENT=FakeChat("fake-5")
        ),
    ),
}
