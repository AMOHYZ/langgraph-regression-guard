import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, TypedDict


@dataclass(frozen=True)
class Step:
    node: str
    output: Any


@dataclass
class Cassette:
    inputs: dict[str, Any]
    steps: list[Step]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Cassette":
        data = json.loads(text)
        return cls(inputs=data["inputs"], steps=[Step(**s) for s in data["steps"]])


@dataclass(frozen=True)
class Drift:
    node: str
    expected: list[str] | None
    actual: list[str] | None


def record(graph: Any, inputs: dict[str, Any]) -> Cassette:
    """Run the graph once and save every node's output, in order.

    graph is typed Any on purpose: LangGraph's generic types change between
    versions and would break mypy --strict on upgrades. A Protocol can replace
    it later.
    """
    steps: list[Step] = []
    for chunk in graph.stream(inputs, stream_mode="updates"):
        for node, update in chunk.items():
            steps.append(Step(node=node, output=update))
    return Cassette(inputs=inputs, steps=steps)


def _by_node(steps: list[Step]) -> dict[str, list[str]]:
    # sorted per node so parallel Send workers compare stably
    out: dict[str, list[str]] = {}
    for s in steps:
        out.setdefault(s.node, []).append(json.dumps(s.output, sort_keys=True))
    return {n: sorted(v) for n, v in out.items()}


def replay(graph: Any, cassette: Cassette) -> list[Drift]:
    """Re-run with the recorded inputs; return nodes whose outputs changed."""
    expected = _by_node(cassette.steps)
    actual = _by_node(record(graph, cassette.inputs).steps)
    return [
        Drift(node=n, expected=expected.get(n), actual=actual.get(n))
        for n in sorted(expected.keys() | actual.keys())
        if expected.get(n) != actual.get(n)
    ]


# A node function: takes the incoming state, returns a partial state update.
# Param is Ellipsis because user nodes are typed against their own schema.
NodeFunc = Callable[..., dict[str, Any]]


class NodeIO(TypedDict):
    # Inputs and outputs are arbitrary user state payloads (Send() payloads
    # especially can be any value), so they deliberately stay Any.
    input: Any
    output: Any


def record_io(
    compile_fn: Callable[[dict[str, NodeFunc]], Any],
    nodes: dict[str, NodeFunc],
    inp: dict[str, Any],
) -> dict[str, NodeIO]:
    """Run the graph once, capturing each node's exact input and output.

    Node functions are wrapped before compilation so the recorded input is the
    state the node actually received (including Send() payloads), which
    stream-mode updates alone cannot see. compile_fn returns Any for the same
    reason as record's graph parameter.
    """
    log: dict[str, NodeIO] = {}

    def capture(name: str, fn: NodeFunc) -> NodeFunc:
        def inner(state: dict[str, Any]) -> dict[str, Any]:
            node_input = deepcopy(state)
            out = fn(state)
            log[name] = NodeIO(input=node_input, output=deepcopy(out))
            return out

        return inner

    graph = compile_fn({n: capture(n, f) for n, f in nodes.items()})
    graph.invoke(inp)
    return log


def diff_cassettes(base: dict[str, NodeIO], new: dict[str, NodeIO]) -> dict[str, str]:
    """Classify per-node drift.

    "root": same input, different output (the node you edited).
    "propagated": different input (it only changed because upstream did).
    "added"/"removed": the node exists on only one side.
    """
    drift: dict[str, str] = {}
    for name in base.keys() | new.keys():
        b, n = base.get(name), new.get(name)
        if b is None or n is None:
            drift[name] = "added" if b is None else "removed"
        elif b["input"] != n["input"]:
            drift[name] = "propagated"
        elif b["output"] != n["output"]:
            drift[name] = "root"
    return drift
