import json
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from copy import copy, deepcopy
from dataclasses import asdict, dataclass, field
from statistics import fmean
from types import CodeType, FunctionType
from typing import Any, Literal, TypedDict

import httpx
from langgraph.errors import GraphBubbleUp
from langgraph.graph import START
from langgraph.pregel import Pregel

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
    return Cassette(inputs=inputs, steps=_run(graph, inputs, (), ())[0])


def _key(value: Any, ignore_paths: Sequence[str]) -> str | None:
    # Canonical JSON of the masked value; None if it is not JSON, so never kept or served.
    try:
        return json.dumps(normalize(value, ignore_paths), sort_keys=True)
    except (TypeError, ValueError):
        return None


# Network trouble is not the agent's fault, even when it surfaces inside a node (T074).
TRANSPORT_ERRORS = (TimeoutError, ConnectionError, httpx.TransportError)


class NodeError(Exception):
    """A node's own code raised during replay: the agent broke, unless `transport`."""

    def __init__(self, node: str, original: Exception) -> None:
        super().__init__(f"node `{node}` raised {type(original).__name__}: {original}")
        self.node = node
        self.original = original
        self.transport = isinstance(original, TRANSPORT_ERRORS)


# T089: what replay does when a node's code matches the recording but its input doesn't.
OnDivergence = Literal["fail", "flag", "live"]
ON_DIVERGENCE: tuple[OnDivergence, ...] = ("fail", "flag", "live")


@dataclass(frozen=True)
class Call:
    """T089: one node call in a replay. `diverged`: the first call in its run whose code
    matched the recording but whose input didn't, so it ran live; `after_divergence`: a
    call that ran live after that."""

    node: str
    how: Literal["served", "live", "diverged", "after_divergence"]
    seconds: float = 0.0


@dataclass(frozen=True)
class Halt:
    """T089: where a replayed run stopped. That node and the rest of the run aren't checked."""

    node: str
    why: Literal["diverged", "budget"]


class _Halted(Exception):
    def __init__(self, halt: Halt) -> None:
        super().__init__(f"replay stopped at `{halt.node}` ({halt.why})")
        self.halt = halt


def over_budget(elapsed: float, live: Sequence[float], remaining: int, budget: float) -> bool:
    """T089: elapsed time plus the mean live call times the calls still to come is over."""
    return elapsed + fmean(live) * remaining > budget


@dataclass
class Budget:
    """T089: one wall-clock budget for a run's live node calls. It is checked before each
    live call, never during one: a call that has started finishes."""

    seconds: float | None
    clock: Callable[[], float] = time.perf_counter
    expected_live: int = 0  # live calls expected in all, from `unservable`
    live: list[float] = field(default_factory=list)  # each live call's seconds so far
    hit: tuple[str, float] | None = None  # the node it stopped at, and seconds elapsed
    start: float = field(init=False)

    def __post_init__(self) -> None:
        self.start = self.clock()

    def allows(self, node: str) -> bool:
        if self.hit is not None:
            return False
        if self.seconds is None or not self.live:
            return True  # nothing measured yet: the first live call always runs
        elapsed = self.clock() - self.start
        if over_budget(
            elapsed, self.live, max(1, self.expected_live - len(self.live)), self.seconds
        ):
            self.hit = (node, elapsed)
            return False
        return True


def _codes(graph: Any) -> dict[str, str | None]:
    # The plain-function nodes replay wraps, by name; subgraphs and the like run untouched.
    codes: dict[str, str | None] = {}
    for name, spec in getattr(graph, "nodes", {}).items():
        fn = getattr(spec.bound, "func", None)
        if name != START and isinstance(fn, FunctionType):
            codes[name] = fingerprint(fn)
    return codes


def _routers(graph: Any, prefix: str = "") -> dict[CodeType, str]:
    """T109: each conditional edge's router function, by its code, named as its branch is
    (`<subgraph node>.<branch>` inside a subgraph)."""
    named: dict[CodeType, str] = {}
    branches = getattr(getattr(graph, "builder", None), "branches", None) or {}
    for per_source in branches.values():
        for name, branch in per_source.items():
            code = getattr(getattr(getattr(branch, "path", None), "func", None), "__code__", None)
            if isinstance(code, CodeType):
                named[code] = f"{prefix}{name}"
    for name, spec in getattr(graph, "nodes", {}).items():
        if isinstance(getattr(spec, "bound", None), Pregel):
            named.update(_routers(spec.bound, f"{prefix}{name}."))
    return named


def _raised_in(exc: BaseException, routers: Mapping[CodeType, str]) -> str | None:
    # The router whose frame is deepest in the traceback: the one that raised, if any. A key
    # its mapping lacks raises after it returns, in a LangGraph frame whose `self` is the
    # branch. Only LangGraph's frames are read that way: the agent's objects run no code here.
    found, tb = None, exc.__traceback__
    while tb is not None:
        frame = tb.tb_frame
        if frame.f_globals.get("__name__", "").startswith("langgraph."):
            path = getattr(frame.f_locals.get("self"), "path", None)
            code = getattr(getattr(path, "func", None), "__code__", None)
            if isinstance(code, CodeType):
                found = routers.get(code, found)
        found = routers.get(frame.f_code, found)
        tb = tb.tb_next
    return found


def _naming(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    def call(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (GraphBubbleUp, NodeError):
            raise  # LangGraph's own control flow, or a node further in, already named
        except Exception as exc:
            raise NodeError(name, exc) from exc

    return call


def _attributed(graph: Any, prefix: str) -> Any:
    """T109: a copy of a subgraph whose nodes' errors name them `<prefix><node>`. It runs
    as it is otherwise: a subgraph's nodes are never served."""
    original = getattr(graph, "nodes", {})
    nodes = dict(original)
    for name, spec in original.items():
        fn = getattr(spec.bound, "func", None)
        if name != START and isinstance(fn, FunctionType):
            bound = copy(spec.bound)
            bound.func = _naming(f"{prefix}{name}", fn)
            nodes[name] = spec.copy({"bound": bound})
        elif isinstance(spec.bound, Pregel):
            nodes[name] = spec.copy({"bound": _attributed(spec.bound, f"{prefix}{name}.")})
    return graph.copy({"nodes": nodes}) if original else graph


def unservable(graph: Any, cassettes: Sequence[Cassette]) -> int:
    """T089: recorded steps replay can't serve, since their node's code changed or they
    have no input or fingerprint: the live calls a budget projects from."""
    codes = _codes(graph)
    return sum(
        1
        for c in cassettes
        for s in c.steps
        if s.code is None or s.input is None or codes.get(s.node) != s.code
    )


def _run(
    graph: Any,
    inputs: dict[str, Any],
    served: Sequence[Step],
    ignore_paths: Sequence[str],
    on_divergence: OnDivergence = "flag",
    budget: Budget | None = None,
) -> tuple[list[Step], list[Call], Halt | None]:
    """Stream the graph once; return its steps with each node's input and fingerprint, how
    each call ran, and where the run stopped, if it stopped early.

    Each plain-function node is wrapped: a call whose fingerprint and masked input match an
    unused step of `served` returns that step's output instead of running the node.
    """
    lock = threading.Lock()  # Send() fan-out runs nodes in parallel threads
    unused = [(s, _key(s.input, ignore_paths)) for s in served if s.code is not None]
    recorded = {(s.node, s.code) for s in served if s.code is not None}
    calls: list[tuple[str, Any, Any, str | None]] = []  # node, input, returned, fingerprint
    log: list[Call] = []
    run = {"diverged": False, "live": False}  # diverged yet; on_divergence="live" took over
    clock = time.perf_counter if budget is None else budget.clock

    def wrap(name: str, fn: Callable[..., Any], code: str | None) -> Callable[..., Any]:
        def call(state: Any, *args: Any, **kwargs: Any) -> Any:
            key = _key(state, ignore_paths)
            snapshot = deepcopy(state) if key is not None else None  # before fn can mutate it
            hit: Step | None = None
            diverged = False
            if code is not None and key is not None:
                with lock:
                    want = (name, code, key)
                    i = next(
                        (i for i, (s, k) in enumerate(unused) if (s.node, s.code, k) == want), None
                    )
                    if i is not None and not run["live"]:
                        hit = unused.pop(i)[0]
                    diverged = hit is None and not run["diverged"] and (name, code) in recorded
                    run["diverged"] = run["diverged"] or diverged
            how: Literal["served", "live", "diverged", "after_divergence"] = "served"
            seconds = 0.0
            if hit is not None:
                out = deepcopy(hit.output)
            else:
                how = "diverged" if diverged else "after_divergence" if run["diverged"] else "live"
                if diverged and on_divergence == "fail":
                    raise _Halted(Halt(name, "diverged"))
                if diverged and on_divergence == "live":
                    run["live"] = True
                if budget is not None and not budget.allows(name):
                    raise _Halted(Halt(name, "budget"))
                start = clock()
                try:
                    out = fn(state, *args, **kwargs)
                except GraphBubbleUp:
                    raise  # LangGraph's own control flow (interrupt, Command), not a crash
                except Exception as exc:
                    raise NodeError(name, exc) from exc
                seconds = clock() - start
            with lock:
                log.append(Call(name, how, seconds))
                if budget is not None and hit is None:
                    budget.live.append(seconds)
                if key is not None:
                    calls.append((name, snapshot, out, code))
            return out

        return call

    original = getattr(graph, "nodes", {})  # an object with only .stream() runs as it is
    nodes = dict(original)
    for name, code in _codes(graph).items():
        spec = original[name]
        bound = copy(spec.bound)  # LangGraph's node runnable calls .func on invoke
        bound.func = wrap(name, spec.bound.func, code)
        nodes[name] = spec.copy({"bound": bound})
    for name, spec in original.items():  # T109: a subgraph's errors name its own node
        if isinstance(getattr(spec, "bound", None), Pregel):
            nodes[name] = spec.copy({"bound": _attributed(spec.bound, f"{name}.")})
    routers = _routers(graph)
    runner = graph.copy({"nodes": nodes}) if original else graph
    steps: list[Step] = []
    try:
        for chunk in runner.stream(inputs, stream_mode="updates"):
            for node, update in chunk.items():
                with lock:  # the call that made this update (a returned {} streams as None)
                    i = next(
                        (i for i, c in enumerate(calls) if (c[0], c[2] or None) == (node, update)),
                        None,
                    )
                    call = None if i is None else calls.pop(i)
                steps.append(
                    Step(node, update) if call is None else Step(node, update, call[1], call[3])
                )
    except _Halted as stop:
        return steps, log, stop.halt
    except (NodeError, GraphBubbleUp):
        raise
    except Exception as exc:  # T109: a router raised: name it, as a node's error names the node
        router = _raised_in(exc, routers)
        if router is None:
            raise
        raise NodeError(router, exc) from exc
    return steps, log, None


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
    return replay_steps(graph, cassette, ignore_paths, serve)[0]


def replay_steps(
    graph: Any, cassette: Cassette, ignore_paths: Sequence[str] = (), serve: bool = False
) -> tuple[list[Drift], list[Step]]:
    """`replay`, plus the fresh run's steps in order (served ones included), so coverage
    can reuse the run instead of executing the graph again.
    """
    r = replay_run(graph, cassette, ignore_paths, serve)
    return list(r.drifts), list(r.steps)


@dataclass(frozen=True)
class Replay:
    """T089: one cassette replayed: drift, the fresh steps, how each call ran and, if the
    run stopped early, the nodes from there on that weren't checked."""

    drifts: tuple[Drift, ...]
    steps: tuple[Step, ...]
    calls: tuple[Call, ...]
    halt: Halt | None = None
    not_checked: tuple[str, ...] = ()  # the halted node first, then the recorded ones after


def replay_run(
    graph: Any,
    cassette: Cassette,
    ignore_paths: Sequence[str] = (),
    serve: bool = False,
    on_divergence: OnDivergence = "flag",
    budget: Budget | None = None,
) -> Replay:
    """`replay_steps` under a divergence policy and a budget, with how each call ran."""
    served = cassette.steps if serve else ()
    fresh, calls, halt = _run(graph, cassette.inputs, served, ignore_paths, on_divergence, budget)
    recorded = cassette.steps
    not_checked: tuple[str, ...] = ()
    if halt is not None:  # compare only what the run reached: each node's first k steps
        reached, seen = Counter(s.node for s in fresh), Counter[str]()
        recorded = []
        for s in cassette.steps:
            if seen[s.node] < reached[s.node]:
                recorded.append(s)
                seen[s.node] += 1
        later = [n for n in dict.fromkeys(s.node for s in cassette.steps) if n not in reached]
        not_checked = (halt.node, *(n for n in later if n != halt.node))
    expected, actual = _by_node(recorded, ignore_paths), _by_node(fresh, ignore_paths)
    drifts = tuple(
        Drift(node=n, expected=expected.get(n), actual=actual.get(n))
        for n in sorted(expected.keys() | actual.keys())
        if expected.get(n) != actual.get(n)
    )
    return Replay(drifts, tuple(fresh), tuple(calls), halt, not_checked)


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
