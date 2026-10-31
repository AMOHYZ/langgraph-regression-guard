import json
import threading
from collections.abc import Callable, Sequence
from copy import copy, deepcopy
from dataclasses import asdict, dataclass
from types import FunctionType
from typing import Any, TypedDict

from langgraph.graph import START

from lgcov.fingerprint import fingerprint
from lgcov.normalize import normalize


@dataclass(frozen=True)
class Step:
    node: str
    output: Any
    # The node's input and code fingerprint when recorded, so replay can serve `output`
    # while both still match. None in cassettes recorded before nodes kept them, and
    # code is None for a node with no fingerprint: such steps always run live.
    input: Any = None
    code: str | None = None


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
    """Run the graph once and save every node's output, in order, with the input and
    code fingerprint `replay` needs to serve it later.

    graph is typed Any on purpose: LangGraph's generic types change between
    versions and would break mypy --strict on upgrades. A Protocol can replace
    it later.
    """
    return Cassette(inputs=inputs, steps=_run(graph, inputs, (), ()))


def _key(value: Any, ignore_paths: Sequence[str]) -> str | None:
    # Canonical JSON of the masked value; None if it is not JSON, so never kept or served.
    try:
        return json.dumps(normalize(value, ignore_paths), sort_keys=True)
    except (TypeError, ValueError):
        return None


def _run(
    graph: Any, inputs: dict[str, Any], served: Sequence[Step], ignore_paths: Sequence[str]
) -> list[Step]:
    """Stream the graph once; return its steps with each node's input and fingerprint.

    Each plain-function node is wrapped: a call whose fingerprint and masked input match an
    unused step of `served` returns that step's output instead of running the node.
    """
    lock = threading.Lock()  # Send() fan-out runs nodes in parallel threads
    unused = [(s, _key(s.input, ignore_paths)) for s in served if s.code is not None]
    calls: list[tuple[str, Any, Any, str | None]] = []  # node, input, returned, fingerprint

    def wrap(name: str, fn: Callable[..., Any], code: str | None) -> Callable[..., Any]:
        def call(state: Any, *args: Any, **kwargs: Any) -> Any:
            key = _key(state, ignore_paths)
            snapshot = deepcopy(state) if key is not None else None  # before fn can mutate it
            hit: Step | None = None
            if code is not None and key is not None:
                with lock:
                    want = (name, code, key)
                    i = next(
                        (i for i, (s, k) in enumerate(unused) if (s.node, s.code, k) == want), None
                    )
                    hit = None if i is None else unused.pop(i)[0]
            out = deepcopy(hit.output) if hit is not None else fn(state, *args, **kwargs)
            if key is not None:
                with lock:
                    calls.append((name, snapshot, out, code))
            return out

        return call

    original = getattr(graph, "nodes", {})  # an object with only .stream() runs as it is
    nodes = dict(original)
    for name, spec in original.items():
        fn = getattr(spec.bound, "func", None)
        if name != START and isinstance(fn, FunctionType):  # subgraphs etc. run untouched
            bound = copy(spec.bound)  # LangGraph's node runnable calls .func on invoke
            bound.func = wrap(name, fn, fingerprint(fn))
            nodes[name] = spec.copy({"bound": bound})
    runner = graph.copy({"nodes": nodes}) if original else graph
    steps: list[Step] = []
    for chunk in runner.stream(inputs, stream_mode="updates"):
        for node, update in chunk.items():
            with lock:  # the call that made this update (a returned {} streams as None)
                i = next(
                    (i for i, c in enumerate(calls) if (c[0], c[2] or None) == (node, update)), None
                )
                call = None if i is None else calls.pop(i)
            steps.append(
                Step(node, update) if call is None else Step(node, update, call[1], call[3])
            )
    return steps


def _by_node(steps: list[Step], ignore_paths: Sequence[str] = ()) -> dict[str, list[str]]:
    # sorted per node so parallel Send workers compare stably; T085: volatile fields masked
    out: dict[str, list[str]] = {}
    for s in steps:
        norm = normalize(s.output, ignore_paths)
        out.setdefault(s.node, []).append(json.dumps(norm, sort_keys=True))
    return {n: sorted(v) for n, v in out.items()}


def replay(
    graph: Any, cassette: Cassette, ignore_paths: Sequence[str] = (), serve: bool = False
) -> list[Drift]:
    """Re-run with the recorded inputs; return nodes whose (normalised) outputs changed.

    serve=True (what `lgcov replay` does) skips the nodes a change left alone: a node whose
    code fingerprint (see lgcov.fingerprint) and normalised input both match a recorded
    step returns that step's output without running. A node whose code changed, and any
    node whose input changed because of it, still runs and is compared.
    """
    expected = _by_node(cassette.steps, ignore_paths)
    fresh = _run(graph, cassette.inputs, cassette.steps if serve else (), ignore_paths)
    actual = _by_node(fresh, ignore_paths)
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
        if new.output != step.output:  # a new input or code alone is no drift
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
