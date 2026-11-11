import asyncio
import atexit
import inspect
import json
import socket
import threading
import time
from collections import Counter
from collections.abc import Callable, Container, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import copy_context
from copy import copy, deepcopy
from dataclasses import asdict, dataclass, field, replace
from functools import partial, wraps
from statistics import fmean
from types import CodeType, FunctionType
from typing import Any, Literal, NoReturn, TypedDict

import httpx
from langgraph.errors import GraphBubbleUp
from langgraph.graph import START
from langgraph.pregel import Pregel
from langgraph.types import Command, Send

from lgcov.attribution import NEEDS_LIVE, NO_FINGERPRINT, Probe, drift_exceeds_noise
from lgcov.fingerprint import fingerprint
from lgcov.messages import decode, encode
from lgcov.normalize import key_view, normalize
from lgcov.rebind import Ambiguous, rebind


@dataclass(frozen=True)
class Step:
    node: str
    output: Any
    # The node's input and code fingerprint when recorded, so replay can serve `output`
    # while both still match. None in cassettes recorded before nodes kept them, and
    # code is None for a node with no fingerprint: such steps always run live.
    input: Any = None
    code: str | None = None
    # T168: where the node sent the run, if it returned a Command: node names, and {"send":
    # node, "arg": payload} for a Send. None for any other return, and in cassettes recorded
    # before this was kept.
    goto: list[Any] | None = None


@dataclass
class Cassette:
    inputs: dict[str, Any]
    steps: list[Step]

    def body(self) -> dict[str, Any]:
        """What's saved. A step with no goto has no such key, so a cassette with no Command in
        it saves byte for byte as it did before goto was kept."""
        body = asdict(self)
        for step in body["steps"]:
            if step["goto"] is None:
                del step["goto"]
        return body

    def to_json(self) -> str:
        return json.dumps(self.body(), indent=2, sort_keys=True, default=encode) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Cassette":
        data = json.loads(text, object_hook=decode)
        return cls(inputs=data["inputs"], steps=[Step(**s) for s in data["steps"]])


def runs_to_json(runs: Sequence[Cassette]) -> str:
    # Same shape and formatting as examples/demo_agent/record.py, so baselines diff cleanly.
    body = {"runs": [r.body() for r in runs]}
    return json.dumps(body, indent=2, sort_keys=True, default=encode) + "\n"


def runs_from_json(text: str) -> list[Cassette]:
    # Any: raw JSON before validation
    data: Any = json.loads(text, object_hook=decode)
    return [
        Cassette(inputs=r["inputs"], steps=[Step(**s) for s in r["steps"]]) for r in data["runs"]
    ]


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


def _key(value: Any, ignore_paths: Sequence[str], mask_volatile: bool) -> str | None:
    # Canonical JSON of the masked value; None if it is not JSON, so never kept or served.
    try:
        return json.dumps(key_view(value, ignore_paths, mask_volatile), sort_keys=True)
    except (TypeError, ValueError):
        return None


# Network trouble is not the agent's fault, even when it surfaces inside a node (T074).
TRANSPORT_ERRORS = (TimeoutError, ConnectionError, httpx.TransportError)


class NodeError(Exception):
    """A node's own code raised during replay: the agent broke, unless `transport`. T152:
    `attempts`: how many calls to that node raised in this run, as under a retry policy."""

    def __init__(self, node: str, original: Exception, attempts: int = 1) -> None:
        self.raised = f"{type(original).__name__}: {original}"
        if attempts > 1:
            self.raised += f" ({attempts} failed attempts in this run)"
        super().__init__(f"node `{node}` raised {self.raised}")
        self.node = node
        self.original = original
        self.attempts = attempts
        self.transport = isinstance(original, TRANSPORT_ERRORS)


# T089: what replay does when a node's code matches the recording but its input doesn't.
OnDivergence = Literal["fail", "flag", "live"]
ON_DIVERGENCE: tuple[OnDivergence, ...] = ("fail", "flag", "live")
How = Literal["served", "live", "diverged", "after_divergence"]


@dataclass(frozen=True)
class Call:
    """T089: one node call in a replay. `diverged`: the first call in its run whose code
    matched the recording but whose input didn't, so it ran live; `after_divergence`: a
    call that ran live after that. T165: `rebind_skipped`: it matched a recorded step, but
    that step's ids couldn't be fitted to this run's, so it ran live instead of being served.
    T152: `raised`: it raised. In a run that finished, a retry policy ran the node again or an
    error handler took over, and what this call did before raising still happened."""

    node: str
    how: How
    seconds: float = 0.0
    rebind_skipped: bool = False
    raised: bool = False


@dataclass(frozen=True)
class Halt:
    """T089: where a replayed run stopped. That node and the rest of the run aren't checked."""

    node: str
    why: Literal["diverged", "budget"]


class RunningLoopError(RuntimeError):
    """T164: an async graph needs its own event loop, and this thread already runs one."""

    def __init__(self) -> None:
        super().__init__(
            "lgcov can't run an async graph inside a running event loop (a notebook, an async "
            "test): use the lgcov CLI, or call this from outside the running loop"
        )


class _Halted(RuntimeError):
    # T152: LangGraph's default retry rule refuses a RuntimeError, so a node with a retry policy
    # doesn't sleep before a halt reaches `_run`. A rule that retries anything meets it again.
    def __init__(self, halt: Halt) -> None:
        super().__init__(f"replay stopped at `{halt.node}` ({halt.why})")
        self.halt = halt


def over_budget(elapsed: float, live: Sequence[float], remaining: int, budget: float) -> bool:
    """T089: elapsed time plus the mean live call times the calls still to come is over."""
    return elapsed + fmean(live) * remaining > budget


@dataclass
class Budget:
    """T089: one wall-clock budget for a run's live node calls. It is checked before each
    live call, never during one: a call that has started finishes. T164: under `.astream`,
    stopping the run cancels a parallel call still in flight, which counts as not checked."""

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


def _plain(bound: Any) -> FunctionType | None:
    # T164: the function behind a node: `func`, or `afunc` on an `async def` node. An async
    # generator has no single call to wrap or serve, so it runs untouched, like a subgraph.
    fn = getattr(bound, "func", None) or getattr(bound, "afunc", None)
    return fn if isinstance(fn, FunctionType) and not inspect.isasyncgenfunction(fn) else None


def _codes(graph: Any) -> dict[str, str | None]:
    # The plain-function nodes replay wraps, by name; subgraphs and the like run untouched.
    codes: dict[str, str | None] = {}
    for name, spec in getattr(graph, "nodes", {}).items():
        fn = _plain(spec.bound)
        if name != START and fn is not None:
            codes[name] = fingerprint(fn)
    return codes


def _only_async(runnable: Any) -> bool:
    # LangGraph's async node or router has func=None; a RunnableLambda's has no func at all.
    return getattr(runnable, "afunc", None) is not None and getattr(runnable, "func", None) is None


def _needs_async(graph: Any) -> bool:
    """T164: `.stream` can't drive this graph: a node or router, here or in a subgraph, has
    only an async body, or the object has only `.astream`."""
    if not hasattr(graph, "stream"):
        return hasattr(graph, "astream")
    bounds = [spec.bound for spec in getattr(graph, "nodes", {}).values()]
    branches = getattr(getattr(graph, "builder", None), "branches", None) or {}
    paths = [getattr(b, "path", None) for per in branches.values() for b in per.values()]
    return any(_only_async(r) for r in (*bounds, *paths)) or any(
        _needs_async(b) for b in bounds if isinstance(b, Pregel)
    )


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


_loops = threading.local()


def _thread_loop() -> asyncio.Runner:
    """T164: this thread's event loop for async runs. It stays open between runs: a client
    kept across them, like a cached model client, holds connections that belong to the loop
    that opened them, and fails with `Event loop is closed` on a new one."""
    runner: asyncio.Runner | None = getattr(_loops, "runner", None)
    if runner is None:
        runner = _loops.runner = asyncio.Runner()
        atexit.register(runner.close)
    return runner


async def _cancel_the_rest() -> None:
    # As closing a loop would: work a node left running can't carry on into the next run.
    rest = asyncio.all_tasks() - {asyncio.current_task()}
    for task in rest:
        task.cancel()
    await asyncio.gather(*rest, return_exceptions=True)


def _router_code(path: Any) -> Any:
    # T164: `func`, or `afunc` on an async router.
    return getattr(getattr(path, "func", None) or getattr(path, "afunc", None), "__code__", None)


def _routers(graph: Any, prefix: str = "") -> dict[CodeType, str]:
    """T109: each conditional edge's router function, by its code, named as its branch is
    (`<subgraph node>.<branch>` inside a subgraph)."""
    named: dict[CodeType, str] = {}
    branches = getattr(getattr(graph, "builder", None), "branches", None) or {}
    for per_source in branches.values():
        for name, branch in per_source.items():
            code = _router_code(getattr(branch, "path", None))
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
            code = _router_code(getattr(frame.f_locals.get("self"), "path", None))
            if isinstance(code, CodeType):
                found = routers.get(code, found)
        found = routers.get(frame.f_code, found)
        tb = tb.tb_next
    return found


@contextmanager
def _noting(failed: Callable[[Exception], None]) -> Iterator[None]:
    # T152: an error from the code inside `with` goes on as it is, so a retry policy or an error
    # handler sees what it sees outside lgcov. `_run` makes it a NodeError when it leaves the run.
    try:
        yield
    except GraphBubbleUp:
        raise  # LangGraph's own control flow
    except Exception as exc:
        failed(exc)
        raise


_Wrap = Callable[[Callable[..., Any]], Callable[..., Any]]


def _around(spec: Any, wrap: _Wrap, awrap: _Wrap) -> Any:
    """T164: a copy of a node with `wrap` on `func`, which `.stream` calls, and `awrap` on
    `afunc`, which `.astream` calls. A sync node's `afunc` is LangGraph's hand-off of the
    original function to a thread, so it would skip a wrapper on `func` alone. Any other
    `afunc` beside a `func` is a second body, which `func`'s fingerprint doesn't cover: left
    untouched, it runs live under `.astream` and its step is never served."""
    bound = copy(spec.bound)  # LangGraph's node runnable calls .func on invoke
    fn, afn = getattr(bound, "func", None), getattr(bound, "afunc", None)
    if fn is not None:
        bound.func = wrap(fn)
    if afn is not None and (fn is None or getattr(afn, "__wrapped__", None) is fn):
        bound.afunc = awrap(afn)
    return spec.copy({"bound": bound})


def _naming(failed: Callable[[Exception], None], fn: Callable[..., Any]) -> Callable[..., Any]:
    def call(*args: Any, **kwargs: Any) -> Any:
        with _noting(failed):
            return fn(*args, **kwargs)

    return call


def _anaming(failed: Callable[[Exception], None], fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(fn)  # a RunnableLambda reads the signature to see what to pass, like config
    async def call(*args: Any, **kwargs: Any) -> Any:
        with _noting(failed):
            return await fn(*args, **kwargs)

    return call


def _attributed(graph: Any, prefix: str, failed: Callable[[str, Exception], None]) -> Any:
    """T109: a copy of a subgraph whose nodes' errors name them `<prefix><node>`. It runs
    as it is otherwise: a subgraph's nodes are never served."""
    original = getattr(graph, "nodes", {})
    nodes = dict(original)
    for name, spec in original.items():
        if name != START and _plain(spec.bound) is not None:
            named = partial(failed, f"{prefix}{name}")
            nodes[name] = _around(spec, partial(_naming, named), partial(_anaming, named))
        elif isinstance(spec.bound, Pregel):
            inner = _attributed(spec.bound, f"{prefix}{name}.", failed)
            nodes[name] = spec.copy({"bound": inner})
    return graph.copy({"nodes": nodes}) if original else graph


def _goto_saved(goto: Any) -> list[Any]:
    # T168: a Command's goto as JSON. It's a node, a Send, or a list of them.
    items = [goto] if isinstance(goto, str | Send) else list(goto)
    return [{"send": g.node, "arg": deepcopy(g.arg)} if isinstance(g, Send) else g for g in items]


def _streamed(returned: Any) -> Any:
    # What `.stream` shows for what a node returned: a Command streams as its update, given as
    # a dict or as pairs, and a returned {} streams as None.
    if isinstance(returned, Command):
        update: Any = returned.update
        pairs = isinstance(update, list | tuple) and all(
            isinstance(p, tuple) and len(p) == 2 for p in update
        )
        returned = dict(update) if pairs else update
    return returned or None


def _returned(step: Step) -> Any:
    # T168: what the node returned when it was recorded, to serve: a Command, if it has a goto.
    output = deepcopy(step.output)
    if step.goto is None:
        return output
    goto = [Send(g["send"], g["arg"]) if isinstance(g, dict) else g for g in deepcopy(step.goto)]
    return Command(goto=goto, update=output)


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
    mask_volatile: bool = False,
) -> tuple[list[Step], list[Call], Halt | None]:
    """Stream the graph once; return its steps with each node's input and fingerprint, how
    each call ran, and where the run stopped, if it stopped early.

    Each plain-function node is wrapped: a call whose fingerprint and masked input match an
    unused step of `served` returns that step's output instead of running the node.

    T164: a graph `.stream` can't drive runs under `.astream` with the same stream mode, on
    this thread's event loop (`_thread_loop`); inside a running one that's a RunningLoopError.

    T165: a served output is fitted to the ids this run made (`lgcov.rebind`). If it can't be,
    the call runs live and its `Call` says so.

    T168: a node that returned a Command gets its Command back when served, update and goto, so
    the run goes where it went.

    T152: a node's retry policy and error handler work as they do outside lgcov. Every attempt
    that raised is in the `Call`s, and a retry can't get a call past a halt.
    """
    lock = threading.Lock()  # Send() fan-out runs nodes in parallel threads
    unused = [(s, _key(s.input, ignore_paths, mask_volatile)) for s in served if s.code is not None]
    recorded = {(s.node, s.code) for s in served if s.code is not None}
    calls: list[tuple[str, Any, Any, str | None]] = []  # node, input, returned, fingerprint
    log: list[Call] = []
    failures: list[tuple[str, Exception]] = []  # each attempt that raised: `Call`s hold no errors
    run = {"diverged": False, "live": False}  # diverged yet; on_divergence="live" took over
    halted: Halt | None = None  # on_divergence="fail" stopped the run here
    clock = time.perf_counter if budget is None else budget.clock

    def failed(
        name: str, exc: Exception, how: How = "live", skipped: bool = False, seconds: float = 0.0
    ) -> None:
        with lock:
            log.append(Call(name, how, seconds, skipped, raised=True))
            failures.append((name, exc))

    def begin(
        name: str, code: str | None, state: Any
    ) -> tuple[Step | None, Callable[..., Any], Callable[..., None]]:
        # How this call runs: the step to serve, if any, what logs the call when it's done, and
        # what logs it if it raises.
        nonlocal halted
        key = _key(state, ignore_paths, mask_volatile)
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
        skipped = False
        if hit is not None:
            try:
                fitted = rebind({"update": hit.output, "goto": hit.goto}, hit.input, state)
                hit = replace(hit, output=fitted["update"], goto=fitted["goto"])
            except Ambiguous:
                hit, skipped = None, True
        how: How = "served"
        if hit is None:
            how = "diverged" if diverged else "after_divergence" if run["diverged"] else "live"
            if diverged and on_divergence == "fail":
                halted = Halt(name, "diverged")
            if halted is not None:  # T152: a retry policy can bring this call back after the halt
                raise _Halted(halted)
            if diverged and on_divergence == "live":
                run["live"] = True
            if budget is not None and not budget.allows(name):
                raise _Halted(Halt(name, "budget"))

        def finish(out: Any, seconds: float = 0.0) -> Any:
            with lock:
                log.append(Call(name, how, seconds, skipped))
                if budget is not None and hit is None:
                    budget.live.append(seconds)
                if key is not None:
                    calls.append((name, snapshot, out, code))
            return out

        return hit, finish, partial(failed, name, how=how, skipped=skipped)

    def wrap(name: str, fn: Callable[..., Any], code: str | None) -> Callable[..., Any]:
        def call(state: Any, *args: Any, **kwargs: Any) -> Any:
            hit, finish, fail = begin(name, code, state)
            if hit is not None:
                return finish(_returned(hit))
            start = clock()
            with _noting(lambda exc: fail(exc, seconds=clock() - start)):
                out = fn(state, *args, **kwargs)
            return finish(out, clock() - start)

        return call

    def awrap(name: str, fn: Callable[..., Any], code: str | None) -> Callable[..., Any]:
        @wraps(fn)  # a RunnableLambda reads the signature to see what to pass, like config
        async def call(state: Any, *args: Any, **kwargs: Any) -> Any:
            hit, finish, fail = begin(name, code, state)
            if hit is not None:
                return finish(_returned(hit))
            start = clock()
            with _noting(lambda exc: fail(exc, seconds=clock() - start)):
                out = await fn(state, *args, **kwargs)
            return finish(out, clock() - start)

        return call

    original = getattr(graph, "nodes", {})  # an object with only .stream() runs as it is
    nodes = dict(original)
    for name, code in _codes(graph).items():
        nodes[name] = _around(
            original[name], partial(wrap, name, code=code), partial(awrap, name, code=code)
        )
    for name, spec in original.items():  # T109: a subgraph's errors name its own node
        if isinstance(getattr(spec, "bound", None), Pregel):
            nodes[name] = spec.copy({"bound": _attributed(spec.bound, f"{name}.", failed)})
    routers = _routers(graph)
    runner = graph.copy({"nodes": nodes}) if original else graph
    steps: list[Step] = []

    def take(chunk: Mapping[str, Any]) -> None:
        for node, update in chunk.items():
            with lock:  # the call that made this update
                i = next(
                    (i for i, c in enumerate(calls) if (c[0], _streamed(c[2])) == (node, update)),
                    None,
                )
                call = None if i is None else calls.pop(i)
            if call is None:
                steps.append(Step(node, update))
                continue
            goto = _goto_saved(call[2].goto) if isinstance(call[2], Command) else None
            steps.append(Step(node, update, call[1], call[3], goto))

    async def drain() -> None:
        try:
            async for chunk in runner.astream(inputs, stream_mode="updates"):
                take(chunk)
        finally:
            await _cancel_the_rest()

    driven_async = _needs_async(graph)
    if driven_async and _loop_running():
        raise RunningLoopError()
    try:
        if driven_async:
            _thread_loop().run(drain(), context=copy_context())  # the caller's, new each run
        else:
            for chunk in runner.stream(inputs, stream_mode="updates"):
                take(chunk)
    except _Halted as stop:
        return steps, log, stop.halt
    except GraphBubbleUp:
        raise
    except Exception as exc:  # the node that raised it, or T109: the router that did
        named = next((n for n, e in failures if e is exc), None) or _raised_in(exc, routers)
        if named is None:
            raise
        raise NodeError(named, exc, sum(n == named for n, _ in failures) or 1) from exc
    return steps, log, None


def _compared(step: Step, ignore_paths: Sequence[str], routed: Container[str]) -> object:
    # T085: volatile fields masked. T168: for a node in `routed`, where it sent the run counts
    # too, in any order, since what it sends to runs in parallel.
    norm = normalize(step.output, ignore_paths)
    if step.node not in routed:
        return norm
    goto = sorted(
        (normalize(g) for g in step.goto or ()), key=lambda g: json.dumps(g, sort_keys=True)
    )
    return {"goto": goto, "update": norm}


def _by_node(
    steps: Sequence[Step], ignore_paths: Sequence[str] = (), routed: Container[str] = ()
) -> dict[str, list[str]]:
    # sorted per node so parallel Send workers compare stably
    out: dict[str, list[str]] = {}
    for s in steps:
        norm = _compared(s, ignore_paths, routed)
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

    @property
    def rebind_skipped(self) -> tuple[str, ...]:
        """T165: nodes run live because their recorded ids couldn't be fitted to this run's."""
        return tuple(c.node for c in self.calls if c.rebind_skipped)

    @property
    def failed_attempts(self) -> tuple[str, ...]:
        """T152: each call that raised while the run went on, by its node."""
        return tuple(c.node for c in self.calls if c.raised)


def replay_run(
    graph: Any,
    cassette: Cassette,
    ignore_paths: Sequence[str] = (),
    serve: bool = False,
    on_divergence: OnDivergence = "flag",
    budget: Budget | None = None,
    mask_volatile: bool = False,
) -> Replay:
    """`replay_steps` under a divergence policy and a budget, with how each call ran."""
    served = cassette.steps if serve else ()
    fresh, calls, halt = _run(
        graph, cassette.inputs, served, ignore_paths, on_divergence, budget, mask_volatile
    )
    return compared_run(cassette, fresh, calls, halt, ignore_paths)


def compared_run(
    cassette: Cassette,
    fresh: Sequence[Step],
    calls: Sequence[Call],
    halt: Halt | None,
    ignore_paths: Sequence[str] = (),
) -> Replay:
    """T179: one run's steps against its cassette. It only reads data, so the job that decides
    can do this with steps another job reports, without the graph."""
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
    # T168: a cassette saved before goto was kept has no route to compare a fresh one with.
    routed = {s.node for s in cassette.steps if s.goto is not None}
    expected = _by_node(recorded, ignore_paths, routed)
    actual = _by_node(fresh, ignore_paths, routed)
    drifts = tuple(
        Drift(node=n, expected=expected.get(n), actual=actual.get(n))
        for n in sorted(expected.keys() | actual.keys())
        if expected.get(n) != actual.get(n)
    )
    return Replay(drifts, tuple(fresh), tuple(calls), halt, not_checked)


class _Refused(BaseException):
    # T162: the probe tried the network. Not an Exception, so an SDK's retry loop can't catch it
    # and sleep before trying again.
    pass


@contextmanager
def _offline(reached: list[str]) -> Iterator[None]:
    # T162: every way out to the network refuses, and says it was tried.
    def refuse(*args: object, **kwargs: object) -> NoReturn:
        reached.append(repr(args[-1])[:80] if args else "")
        raise _Refused("lgcov's probe runs offline (T162)")

    saved = [
        (socket, "getaddrinfo", socket.getaddrinfo),
        (socket.socket, "connect", socket.socket.connect),
        (socket.socket, "connect_ex", socket.socket.connect_ex),
    ]
    for owner, name, _ in saved:
        setattr(owner, name, refuse)
    try:
        yield
    finally:
        for owner, name, original in saved:
            setattr(owner, name, original)


@dataclass(frozen=True)
class ProbeStep:
    """T162: one changed node run with this code on the input base recorded at `runs[run].steps
    [step]`: what it returned, or why it didn't. It says what ran, not what that means, so the job
    that runs a PR's code can hand it over: `judge_probes` compares."""

    run: int
    step: int
    node: str
    output: Any = None
    goto: list[Any] | None = None
    raised: str = ""  # why it couldn't run offline; "" when it returned


def _probe_step(i: int, j: int, step: Step, fn: FunctionType | None) -> ProbeStep:
    if fn is None:
        return ProbeStep(i, j, step.node, raised=NO_FINGERPRINT)
    reached: list[str] = []
    out: Any = None
    try:
        with _offline(reached):
            config: dict[str, object] = {"config": {"configurable": {}}}  # what a node can ask for
            asks = "config" in inspect.signature(fn).parameters
            out = fn(deepcopy(step.input), **(config if asks else {}))
            if inspect.iscoroutine(out):
                out = asyncio.run(out)
    except _Refused:
        pass  # `reached` says so
    except Exception as exc:  # user node code can raise anything
        if not reached:
            return ProbeStep(i, j, step.node, raised=f"raised {type(exc).__name__} on base's input")
    if reached:  # even if the node caught the refusal and went on
        return ProbeStep(i, j, step.node, raised=NEEDS_LIVE)
    goto = _goto_saved(out.goto) if isinstance(out, Command) else None
    return ProbeStep(i, j, step.node, _streamed(out), goto)


def changed_nodes(graph: Any, runs: Sequence[Cassette]) -> set[str]:
    """T162: the recorded nodes whose fingerprint differs in this graph."""
    codes = _codes(graph)
    return {
        s.node
        for r in runs
        for s in r.steps
        if s.code is not None and codes.get(s.node) not in (None, s.code)
    }


def probe_steps(
    graph: Any, runs: Sequence[Cassette], nodes: Container[str]
) -> tuple[ProbeStep, ...]:
    """T162: run each of `nodes` with this graph's code on every input base recorded for it, with
    the network blocked. A node that reaches for the network would need a live call, and one live
    sample can't be told from noise (T211), so its probe is `raised`, never a crash."""
    fns = {name: _plain(spec.bound) for name, spec in getattr(graph, "nodes", {}).items()}
    return tuple(
        _probe_step(i, j, s, fns.get(s.node))
        for i, run in enumerate(runs)
        for j, s in enumerate(run.steps)
        if s.node in nodes
    )


def _merged(old: Probe | None, new: Probe) -> Probe:
    # A node drifts if any of its probes does; it can't be cleared if any couldn't run.
    if old is None or new.drifted or (old.drifted is False and new.drifted is None):
        return new
    return old


def judge_probes(
    runs: Sequence[Cassette], probes: Sequence[ProbeStep], ignore_paths: Sequence[str] = ()
) -> dict[str, Probe]:
    """T162: each probed node: does it drift on base's recorded input (`drift_exceeds_noise`), as
    replay compares it? Pure. ValueError for a probe that matches no recorded step."""
    found: dict[str, Probe] = {}
    for p in probes:
        steps = runs[p.run].steps if 0 <= p.run < len(runs) else []
        recorded = steps[p.step] if 0 <= p.step < len(steps) else None
        if recorded is None or recorded.node != p.node:
            raise ValueError(f"a probe of `{p.node}` matches no recorded step")
        if p.raised:
            probe = Probe(None, p.raised)
        else:
            routed = {p.node} if recorded.goto is not None else set()
            fresh = _compared(Step(p.node, p.output, goto=p.goto), ignore_paths, routed)
            was = _compared(recorded, ignore_paths, routed)
            probe = Probe(drift_exceeds_noise(p.node, was, fresh))
        found[p.node] = _merged(found.get(p.node), probe)
    return found


def probe_nodes(
    graph: Any, runs: Sequence[Cassette], nodes: Container[str], ignore_paths: Sequence[str] = ()
) -> dict[str, Probe]:
    """T162: `probe_steps` and `judge_probes` in one process, as `lgcov bench` uses them."""
    return judge_probes(runs, probe_steps(graph, runs, nodes), ignore_paths)


def accept_nodes(
    baseline: Cassette, fresh: Cassette, nodes: set[str], ignore_paths: Sequence[str] = ()
) -> tuple[Cassette, set[str]]:
    """T082: copy `fresh` steps into `baseline` for `nodes` only; every other step is kept.

    Returns the new baseline and the subset of `nodes` whose output changed, masked as replay
    compares it. A node must have run the same number of times in both, since steps are matched by
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
        # a new input or code alone is no drift, and nor is what replay masks
        routed = () if step.goto is None else (step.node,)
        if _compared(new, ignore_paths, routed) != _compared(step, ignore_paths, routed):
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
