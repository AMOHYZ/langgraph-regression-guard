"""T179: what the job that runs a PR's code hands the job that decides (pure).

That code can write anything in this file, so it says what ran, never what that means: no drift,
no verdict, no exit code. The job that decides compares the steps with the PR's cassette itself,
and refuses a file that isn't exactly this shape. The steps are still only that code's word for
what it did: docs/trust-boundary.md says what that leaves open.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from typing import Literal

from lgcov.cassette import Call, Cassette, Halt, How, ProbeStep, Step
from lgcov.messages import decode, encode
from lgcov.snapshot import GraphSnapshot

SCHEMA = 2  # T162: 2 adds the probes. What this lgcov writes
# T193: 3 holds units, probes by a unit's position, head's parts and its repeats. Read before
# anything writes it, as 2 was (#114 before #113), so a deciding job on this lgcov can read it.
READS = (2, 3)
_HOWS: tuple[How, ...] = ("served", "live", "diverged", "after_divergence")
_WHYS: tuple[Literal["diverged", "budget"], ...] = ("diverged", "budget")
_CALL = {"node", "how", "seconds", "rebind_skipped", "raised"}
_RUN = {"steps", "calls", "halt"}
_STEP = {"node", "output", "input", "code", "goto", "router"}  # T193's unit fields aren't schema 2
_UNIT = _STEP | {"kind", "units", "routed", "router_state", "parts", "call_id", "status", "samples"}
_KINDS = (None, "node", "tool", "agent")
_PROBE = {"run", "step", "node", "output", "goto", "raised"}
_PROBE_3 = _PROBE - {"step"} | {"unit"}  # T193: a unit's position down the tree, not a step index
_LATER = {"parts", "repeats"}  # T193: schema 3's own fields, which later PRs fill in


@dataclass(frozen=True)
class FreshRun:
    """One cassette's replay, as it ran: `compared_run` makes the rest of a `Replay` from it."""

    steps: tuple[Step, ...]
    calls: tuple[Call, ...]
    halt: Halt | None = None


@dataclass(frozen=True)
class CandidateRun:
    """The half of a gate run that runs the PR's code. A harness error or a crash leaves no runs,
    and `head` None means the graph never loaded."""

    commit: str = ""  # the checkout it ran on
    tier: str = ""
    head: GraphSnapshot | None = None
    graph_lines: tuple[str, ...] = ()  # the base graph runs here too, so its diff comes from here
    graph_note: str = ""
    lint_summary: str = ""
    lint_lines: tuple[str, ...] = ()
    fresh: tuple[FreshRun, ...] = ()
    probes: tuple[ProbeStep, ...] = ()  # T162: each changed node run offline on base's inputs
    # T193: read from schema 3, which this lgcov doesn't write: head's part fingerprints, and its
    # live repeats of a unit for the noise rule (T229), which a user opts into
    parts: dict[str, str | None] | None = None
    repeats: tuple[ProbeStep, ...] = ()
    agent_seconds: float = 0.0
    budget_hit: tuple[str, float] | None = None
    seconds: float = 0.0  # all of it, when it ran in another job
    harness_error: str = ""
    harness_node: str = ""
    crash: str = ""
    crash_node: str = ""


def _saved(value: object) -> object:
    # What JSON can't hold only gets this far under a path lgcov.yaml masks, so nothing compares it.
    try:
        return encode(value)
    except TypeError:
        return repr(value)


def _run_body(run: FreshRun) -> dict[str, object]:
    return {
        "steps": Cassette({}, list(run.steps)).body()["steps"],
        "calls": [asdict(c) for c in run.calls],
        "halt": asdict(run.halt) if run.halt else None,
    }


def _probe_body(probe: ProbeStep, schema: int) -> dict[str, object]:
    body = {k: v for k, v in asdict(probe).items() if k in _PROBE}
    if schema == 3:  # T193: by the unit's position down the tree
        body["unit"] = [body.pop("step"), *(probe.inner or ())]
        if probe.routed is not None:  # T221: a router's probe
            body["routed"] = probe.routed
    return body


def candidate_to_json(run: CandidateRun) -> str:
    """TypeError or ValueError if a step can't be saved, as with a tuple for a dict's key. T193: it
    writes schema 2, no parts or repeats and each probe by its step index, unless a step holds
    units or a probe is a unit's: then schema 3, with each probe by its unit's position. T221: a
    router's probe also needs schema 3, so it can carry `routed`."""
    units = (
        any(s.units for r in run.fresh for s in r.steps)
        or any(
            s.routed is not None or s.router_state is not None for r in run.fresh for s in r.steps
        )
        or any(p.inner for p in run.probes)
        or any(p.routed is not None for p in run.probes)
    )
    schema = 3 if units else SCHEMA
    body: dict[str, object] = asdict(replace(run, head=None, fresh=(), probes=(), repeats=()))
    del body["fresh"], body["probes"], body["parts"], body["repeats"]
    body |= {
        "schema": schema,
        "head": run.head.to_dict() if run.head else None,
        "runs": [_run_body(r) for r in run.fresh],
        "probes": [_probe_body(p, schema) for p in run.probes],
    }
    return json.dumps(body, indent=2, default=_saved) + "\n"


def _text(raw: Mapping[str, object], key: str) -> str:
    value = raw[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} isn't text")
    return value


def _lines(raw: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = raw[key]
    if not isinstance(value, list) or not all(isinstance(line, str) for line in value):
        raise ValueError(f"{key} isn't lines of text")
    return tuple(value)


def _seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{value!r} isn't a number of seconds")
    return float(value)


def _listed(value: object, each: Callable[[object], bool]) -> bool:
    return value is None or isinstance(value, list) and all(map(each, value))


def _sample(value: object) -> bool:
    # T229: an output, and a goto if it has one
    if not isinstance(value, dict) or "output" not in value:
        return False
    return value.keys() <= {"output", "goto"} and isinstance(value.get("goto"), list | None)


def _step(raw: object, schema: int = SCHEMA) -> Step:
    if not isinstance(raw, dict) or not isinstance(raw.get("node"), str):
        raise ValueError("a step has no node")
    keys = _UNIT if schema == 3 else _STEP
    if not raw.keys() <= keys:
        raise ValueError(f"step {raw['node']!r} has fields {sorted(raw.keys() - keys)}")
    units = raw.get("units")
    if not isinstance(units, list | None):
        raise ValueError(f"step {raw['node']!r} has units that aren't a list")
    step = Step(**{**raw, "units": None if units is None else [_step(u, schema) for u in units]})
    texts = (step.code, step.router, step.call_id, step.status)  # T248: success/error/transient
    valid = all(isinstance(v, str | None) for v in texts) and step.kind in _KINDS
    if not valid or not all(isinstance(v, list | None) for v in (step.goto, step.routed)):
        raise ValueError(f"step {step.node!r} has an invalid code, goto, router or unit field")
    if not _listed(step.parts, lambda p: isinstance(p, str)) or not _listed(step.samples, _sample):
        raise ValueError(f"step {step.node!r} has invalid parts or samples")
    return step


def _call(raw: object) -> Call:
    if not isinstance(raw, dict) or set(raw) != _CALL or not isinstance(raw["node"], str):
        raise ValueError(f"not a call: {raw!r}")
    if not isinstance(raw["rebind_skipped"], bool) or not isinstance(raw["raised"], bool):
        raise ValueError(f"not a call: {raw!r}")
    how = next((h for h in _HOWS if h == raw["how"]), None)
    if how is None:
        raise ValueError(f"a call ran as {raw['how']!r}")
    return Call(raw["node"], how, _seconds(raw["seconds"]), raw["rebind_skipped"], raw["raised"])


def _halt(raw: object) -> Halt | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"node", "why"} or not isinstance(raw["node"], str):
        raise ValueError(f"not a halt: {raw!r}")
    why = next((w for w in _WHYS if w == raw["why"]), None)
    if why is None:
        raise ValueError(f"a run stopped for {raw['why']!r}")
    return Halt(raw["node"], why)


def _fresh(raw: object, schema: int = SCHEMA) -> FreshRun:
    if not isinstance(raw, dict) or set(raw) != _RUN:
        raise ValueError(f"a run isn't {sorted(_RUN)}")
    steps, calls = raw["steps"], raw["calls"]
    if not isinstance(steps, list) or not isinstance(calls, list):
        raise ValueError("a run's steps or calls aren't a list")
    return FreshRun(
        tuple(_step(s, schema) for s in steps), tuple(_call(c) for c in calls), _halt(raw["halt"])
    )


def _probe(raw: object, schema: int = SCHEMA) -> ProbeStep:
    """T193: from schema 3 a probe names its unit by `unit`, its position down the tree, read as
    `step` and `inner`, and a router's probe carries `routed` (T221)."""
    want = _PROBE_3 if schema == 3 else _PROBE
    may = want | {"routed"} if schema == 3 else want
    if not isinstance(raw, dict) or not want <= raw.keys() <= may:
        raise ValueError(f"a probe isn't {sorted(want)}")
    if schema == 3:
        at = raw["unit"]
        if not isinstance(at, list) or not at:
            raise ValueError(f"a probe names no unit: {raw!r}")
        rest = {k: v for k, v in raw.items() if k != "unit"}
        raw = {**rest, "step": at[0], "inner": at[1:] or None}
    probe = ProbeStep(**raw)
    numbers = (probe.run, probe.step, *(probe.inner or ()))
    ints = all(isinstance(n, int) and not isinstance(n, bool) for n in numbers)
    texts = isinstance(probe.node, str) and isinstance(probe.raised, str)
    lists = isinstance(probe.goto, list | None) and isinstance(probe.routed, list | None)
    if not ints or not texts or not lists:
        raise ValueError(f"not a probe: {raw!r}")
    return probe


def _parts(raw: object) -> dict[str, str | None] | None:
    # T193: head's parts, each a name with its fingerprint
    if raw is None:
        return None
    if not isinstance(raw, dict) or not all(isinstance(v, str | None) for v in raw.values()):
        raise ValueError("parts aren't names with fingerprints")
    return raw


def _budget_hit(raw: object) -> tuple[str, float] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) != 2 or not isinstance(raw[0], str):
        raise ValueError(f"not a budget hit: {raw!r}")
    return raw[0], _seconds(raw[1])


_FIELDS = {
    *(f.name for f in fields(CandidateRun) if f.name not in {"fresh", *_LATER}),
    "runs",
    "schema",
}


def parse_candidate(text: str) -> CandidateRun:
    """The run in a file. ValueError if it isn't exactly one."""
    try:
        raw: object = json.loads(text, object_hook=decode)
        if not isinstance(raw, dict) or "schema" not in raw:
            raise ValueError("not a candidate file")
        schema = raw["schema"]
        if schema == 1:
            raise ValueError(
                f"candidate file schema 1, evaluator needs {SCHEMA}: "
                "run the same lgcov version in both jobs"
            )
        if type(schema) is not int or schema not in READS:
            reads = " and ".join(map(str, READS))
            raise ValueError(f"candidate file schema {schema!r}; this lgcov reads {reads}")
        may = _FIELDS | _LATER if schema == 3 else _FIELDS
        differ = (_FIELDS - raw.keys()) | (raw.keys() - may)
        if differ:
            raise ValueError(f"its fields differ: {sorted(differ)}")
        if not all(isinstance(raw.get(k, []), list) for k in ("runs", "probes", "repeats")):
            raise ValueError("runs, probes or repeats isn't a list")
        run = CandidateRun(
            commit=_text(raw, "commit"),
            tier=_text(raw, "tier"),
            head=None if raw["head"] is None else GraphSnapshot.from_dict(raw["head"]),
            graph_lines=_lines(raw, "graph_lines"),
            graph_note=_text(raw, "graph_note"),
            lint_summary=_text(raw, "lint_summary"),
            lint_lines=_lines(raw, "lint_lines"),
            fresh=tuple(_fresh(r, schema) for r in raw["runs"]),
            probes=tuple(_probe(p, schema) for p in raw["probes"]),
            parts=_parts(raw.get("parts")),
            repeats=tuple(_probe(p, schema) for p in raw.get("repeats", [])),
            agent_seconds=_seconds(raw["agent_seconds"]),
            budget_hit=_budget_hit(raw["budget_hit"]),
            seconds=_seconds(raw["seconds"]),
            harness_error=_text(raw, "harness_error"),
            harness_node=_text(raw, "harness_node"),
            crash=_text(raw, "crash"),
            crash_node=_text(raw, "crash_node"),
        )
    except (AttributeError, KeyError, TypeError) as exc:
        raise ValueError(f"a field is missing or isn't valid: {exc}") from None
    if run.head is None and not run.harness_error:
        raise ValueError("it has no graph and no error")
    return run


_STOPPED = {
    "diverged": "its input no longer matched the recording",
    "budget": "the time budget ran out",
}


def _runs(n: int) -> str:
    return f"{n} run" if n == 1 else f"{n} runs"


def fresh_cassettes(run: CandidateRun, recorded: Sequence[Cassette]) -> list[Cassette]:
    """T186: the baseline's runs as the candidate job ran them, for `cassette accept
    --candidate-in`: each recorded input, with the steps the file says ran. ValueError when the
    file can't stand in for fresh runs: its job hit a harness error or crashed, it holds another
    number of runs, or a run stopped early. Those steps are still only the PR's code's word
    (docs/trust-boundary.md, limit 1). T193: a schema-3 file's unit fields come along with its
    steps, and nothing writes such a file yet."""
    if run.harness_error:
        raise ValueError(
            "the candidate job hit a harness error, so its file holds no runs to accept"
        )
    if run.crash:
        where = f" at node {run.crash_node!r}" if run.crash_node else ""
        raise ValueError(f"the candidate job's run crashed{where}, so nothing after it ran")
    if len(run.fresh) != len(recorded):
        raise ValueError(
            f"the candidate file holds {_runs(len(run.fresh))} and the baseline "
            f"{_runs(len(recorded))}: they must be the same runs"
        )
    for i, fresh in enumerate(run.fresh):
        if fresh.halt:
            raise ValueError(
                f"run {i} stopped at node {fresh.halt.node!r} as {_STOPPED[fresh.halt.why]}, so "
                "nothing from there on ran"
            )
    return [Cassette(r.inputs, list(f.steps)) for r, f in zip(recorded, run.fresh, strict=True)]
