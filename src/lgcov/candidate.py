"""T179: what the job that runs a PR's code hands the job that decides (pure).

That code can write anything in this file, so it says what ran, never what that means: no drift,
no verdict, no exit code. The job that decides compares the steps with the PR's cassette itself,
and refuses a file that isn't exactly this shape. The steps are still only that code's word for
what it did: docs/trust-boundary.md says what that leaves open.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, replace
from typing import Literal

from lgcov.cassette import Call, Cassette, Halt, How, Step
from lgcov.messages import decode, encode
from lgcov.snapshot import GraphSnapshot

SCHEMA = 1
_HOWS: tuple[How, ...] = ("served", "live", "diverged", "after_divergence")
_WHYS: tuple[Literal["diverged", "budget"], ...] = ("diverged", "budget")
_CALL = {"node", "how", "seconds", "rebind_skipped", "raised"}
_RUN = {"steps", "calls", "halt"}


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


def candidate_to_json(run: CandidateRun) -> str:
    """TypeError or ValueError if a step can't be saved, as with a tuple for a dict's key."""
    body: dict[str, object] = asdict(replace(run, head=None, fresh=()))
    del body["fresh"]
    body |= {
        "schema": SCHEMA,
        "head": run.head.to_dict() if run.head else None,
        "runs": [_run_body(r) for r in run.fresh],
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


def _step(raw: object) -> Step:
    if not isinstance(raw, dict) or not isinstance(raw.get("node"), str):
        raise ValueError("a step has no node")
    step = Step(**raw)
    if not isinstance(step.code, str | None) or not isinstance(step.goto, list | None):
        raise ValueError(f"step {step.node!r} has an invalid code or goto")
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


def _fresh(raw: object) -> FreshRun:
    if not isinstance(raw, dict) or set(raw) != _RUN:
        raise ValueError(f"a run isn't {sorted(_RUN)}")
    steps, calls = raw["steps"], raw["calls"]
    if not isinstance(steps, list) or not isinstance(calls, list):
        raise ValueError("a run's steps or calls aren't a list")
    return FreshRun(
        tuple(_step(s) for s in steps), tuple(_call(c) for c in calls), _halt(raw["halt"])
    )


def _budget_hit(raw: object) -> tuple[str, float] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) != 2 or not isinstance(raw[0], str):
        raise ValueError(f"not a budget hit: {raw!r}")
    return raw[0], _seconds(raw[1])


_FIELDS = {*(f.name for f in fields(CandidateRun) if f.name != "fresh"), "runs", "schema"}


def parse_candidate(text: str) -> CandidateRun:
    """The run in a file. ValueError if it isn't exactly one."""
    try:
        raw: object = json.loads(text, object_hook=decode)
        if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
            raise ValueError(f"not a schema {SCHEMA} candidate file")
        if set(raw) != _FIELDS:
            raise ValueError(f"its fields differ: {sorted(set(raw) ^ _FIELDS)}")
        if not isinstance(raw["runs"], list):
            raise ValueError("runs isn't a list")
        run = CandidateRun(
            commit=_text(raw, "commit"),
            tier=_text(raw, "tier"),
            head=None if raw["head"] is None else GraphSnapshot.from_dict(raw["head"]),
            graph_lines=_lines(raw, "graph_lines"),
            graph_note=_text(raw, "graph_note"),
            lint_summary=_text(raw, "lint_summary"),
            lint_lines=_lines(raw, "lint_lines"),
            fresh=tuple(_fresh(r) for r in raw["runs"]),
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
