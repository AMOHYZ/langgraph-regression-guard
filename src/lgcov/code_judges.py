"""T072: deterministic trajectory judges over a recorded trace.

They implement the Judge protocol and read the run from node_output["trace"]
(any form trace.parse_run accepts). Verdicts are binary with confidence 1.0;
a missing or malformed trace raises JudgeInfraError, never a fail.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass

from lgcov.judges import KPI, Judge, JudgeInfraError, NodeState, Verdict
from lgcov.trace import RunTrace, parse_run


def _run(node_output: NodeState) -> RunTrace:
    if "trace" not in node_output:
        raise JudgeInfraError("code judge: node_output has no 'trace'")
    try:
        return parse_run(node_output["trace"])
    except ValueError as exc:
        raise JudgeInfraError(f"code judge: {exc}") from None


def _verdict(kpi: KPI, ok: bool, reason: str) -> Verdict:
    label = "pass" if ok else "fail"
    return Verdict(kpi, label, kpi.score_map[label], 1.0, {"reason": reason})


def _found(kind: str, name: str, seq: tuple[str, ...]) -> str:
    """'tool web_search called at step 3' or 'tool web_search never called' (1-based)."""
    verb = "called" if kind == "tool" else "visited"
    if name in seq:
        return f"{kind} {name} {verb} at step {seq.index(name) + 1}"
    return f"{kind} {name} never {verb}"


@dataclass(frozen=True)
class NodeVisited:
    kpi: KPI
    node: str

    def judge(self, node_input: NodeState, node_output: NodeState) -> Verdict:
        nodes = _run(node_output).nodes
        return _verdict(self.kpi, self.node in nodes, _found("node", self.node, nodes))


@dataclass(frozen=True)
class NodeNotVisited:
    kpi: KPI
    node: str

    def judge(self, node_input: NodeState, node_output: NodeState) -> Verdict:
        nodes = _run(node_output).nodes
        return _verdict(self.kpi, self.node not in nodes, _found("node", self.node, nodes))


@dataclass(frozen=True)
class ToolCalled:
    kpi: KPI
    tool: str

    def judge(self, node_input: NodeState, node_output: NodeState) -> Verdict:
        tools = _run(node_output).tools
        return _verdict(self.kpi, self.tool in tools, _found("tool", self.tool, tools))


@dataclass(frozen=True)
class ToolNotCalled:
    kpi: KPI
    tool: str

    def judge(self, node_input: NodeState, node_output: NodeState) -> Verdict:
        tools = _run(node_output).tools
        return _verdict(self.kpi, self.tool not in tools, _found("tool", self.tool, tools))


@dataclass(frozen=True)
class MaxSteps:
    kpi: KPI
    n: int

    def judge(self, node_input: NodeState, node_output: NodeState) -> Verdict:
        steps = len(_run(node_output).nodes)
        return _verdict(self.kpi, steps <= self.n, f"{steps} steps, max {self.n}")


def _in_order(kpi: KPI, kind: str, want: tuple[str, ...], seq: tuple[str, ...]) -> Verdict:
    """T197: pass when `want` is found in `seq` in this order; other steps may come between."""
    verb = "called" if kind == "tool" else "visited"
    at = 0  # steps of seq used so far, so also the 1-based step of the last name found
    for i, name in enumerate(want):
        if name not in seq[at:]:
            if i == 0:
                return _verdict(kpi, False, f"{kind} {name} never {verb}")
            return _verdict(kpi, False, f"{kind} {name} not {verb} after {want[i - 1]} (step {at})")
        at += seq[at:].index(name) + 1
    return _verdict(kpi, True, f"{kind}s {', '.join(want)} {verb} in order")


@dataclass(frozen=True)
class NodeSequence:
    kpi: KPI
    nodes: tuple[str, ...]

    def judge(self, node_input: NodeState, node_output: NodeState) -> Verdict:
        return _in_order(self.kpi, "node", self.nodes, _run(node_output).nodes)


@dataclass(frozen=True)
class ToolSequence:
    kpi: KPI
    tools: tuple[str, ...]

    def judge(self, node_input: NodeState, node_output: NodeState) -> Verdict:
        return _in_order(self.kpi, "tool", self.tools, _run(node_output).tools)


def _names(value: str) -> tuple[str, ...]:
    names = tuple(name.strip() for name in value.split(","))
    if not all(names):
        raise ValueError(f"a sequence needs comma-separated names, got {value!r}")
    return names


def _positive_int(value: str) -> int:
    if not value.isdigit() or int(value) < 1:
        raise ValueError(f"'n' must be a positive integer, got {value!r}")
    return int(value)


# check type -> (required param, builder taking the KPI and that param's raw value)
CODE_CHECKS: dict[str, tuple[str, Callable[[KPI, str], Judge]]] = {
    "max_steps": ("n", lambda k, v: MaxSteps(k, _positive_int(v))),
    "node_not_visited": ("node", NodeNotVisited),
    "node_sequence": ("nodes", lambda k, v: NodeSequence(k, _names(v))),
    "node_visited": ("node", NodeVisited),
    "tool_called": ("tool", ToolCalled),
    "tool_not_called": ("tool", ToolNotCalled),
    "tool_sequence": ("tools", lambda k, v: ToolSequence(k, _names(v))),
}
_SEQUENCES = {"node_sequence": "nodes", "tool_sequence": "tools"}


def unknown_names(
    check: Mapping[str, str], base: Collection[str], head: Collection[str]
) -> tuple[str, ...]:
    """T197: a sequence check's names that neither the base nor the PR has: a typo, or a node
    inside a subgraph. There is nothing to judge them on. A name the base has and the PR lost
    is not one of these: the check runs, and fails on the PR."""
    param = _SEQUENCES.get(check.get("type", ""))
    if param is None:
        return ()
    names = dict.fromkeys(_names(check.get(param, "")))
    return tuple(n for n in names if n not in base and n not in head)


def code_judge(kpi: KPI, check: Mapping[str, str]) -> Judge:
    """Build the code judge a taxonomy `check:` mapping asks for."""
    if set(kpi.labels) != {"pass", "fail"}:
        raise ValueError(f"code judges need pass/fail labels, got {kpi.labels}")
    kind = check.get("type", "")
    if kind not in CODE_CHECKS:
        raise ValueError(f"unknown code check {kind!r}, expected one of {sorted(CODE_CHECKS)}")
    param, build = CODE_CHECKS[kind]
    if not check.get(param):
        raise ValueError(f"code check '{kind}' needs '{param}'")
    return build(kpi, check[param])
