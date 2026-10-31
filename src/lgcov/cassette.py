import json
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, TypedDict

from lgcov.normalize import normalize


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


def _by_node(steps: list[Step], ignore_paths: Sequence[str] = ()) -> dict[str, list[str]]:
    # sorted per node so parallel Send workers compare stably; T085: volatile fields masked
    out: dict[str, list[str]] = {}
    for s in steps:
        norm = normalize(s.output, ignore_paths)
        out.setdefault(s.node, []).append(json.dumps(norm, sort_keys=True))
    return {n: sorted(v) for n, v in out.items()}


def replay(graph: Any, cassette: Cassette, ignore_paths: Sequence[str] = ()) -> list[Drift]:
    """Re-run with the recorded inputs; return nodes whose (normalised) outputs changed."""
    expected = _by_node(cassette.steps, ignore_paths)
    actual = _by_node(record(graph, cassette.inputs).steps, ignore_paths)
    return [
        Drift(node=n, expected=expected.get(n), actual=actual.get(n))
        for n in sorted(expected.keys() | actual.keys())
        if expected.get(n) != actual.get(n)
    ]


def accept_nodes(baseline: Cassette, fresh: Cassette, nodes: set[str]) -> tuple[Cassette, set[str]]:
    """T082: copy `fresh` steps into `baseline` for `nodes` only; every other step is kept.

    Returns the new baseline and the subset of `nodes` whose output actually changed.
    A node must have run the same number of times in both, since steps are matched by
    occurrence. Note a fresh output already reflects any upstream drift not accepted.
    """
    by_node: dict[str, list[Step]] = {}
    for step in fresh.steps:
        by_node.setdefault(step.node, []).append(step)
    seen: dict[str, int] = {}
    for step in baseline.steps:
        seen[step.node] = seen.get(step.node, 0) + 1
    for node in sorted(nodes):
        if len(by_node.get(node, [])) != seen.get(node, 0):
            raise ValueError(
                f"node {node!r} ran {len(by_node.get(node, []))}x, baseline has "
                f"{seen.get(node, 0)}x; re-record the baseline instead"
            )
    used: dict[str, int] = {}
    steps: list[Step] = []
    changed: set[str] = set()
    for step in baseline.steps:
        if step.node not in nodes:
            steps.append(step)
            continue
        i = used.get(step.node, 0)
        used[step.node] = i + 1
        new = by_node[step.node][i]
        if new != step:
            changed.add(step.node)
        steps.append(new)
    return Cassette(inputs=baseline.inputs, steps=steps), changed


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


def diff_cassettes(
    base: dict[str, NodeIO], new: dict[str, NodeIO], ignore_paths: Sequence[str] = ()
) -> dict[str, str]:
    """Classify per-node drift, after masking volatile fields (T085).

    "root": same input, different output (the node you edited).
    "propagated": different input (it only changed because upstream did).
    "added"/"removed": the node exists on only one side.
    """
    drift: dict[str, str] = {}
    for name in base.keys() | new.keys():
        b, n = base.get(name), new.get(name)
        if b is None or n is None:
            drift[name] = "added" if b is None else "removed"
        elif normalize(b["input"], ignore_paths) != normalize(n["input"], ignore_paths):
            drift[name] = "propagated"
        elif normalize(b["output"], ignore_paths) != normalize(n["output"], ignore_paths):
            drift[name] = "root"
    return drift
