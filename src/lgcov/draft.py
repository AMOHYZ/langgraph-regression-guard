"""T106: a taxonomy drafted from traces, for a human to approve. Draft, don't trust.

Failing traces are grouped by exact failure signature (the failing node and the failure's
kind), one failure mode per group. A pluggable client (an LLM; a scripted fake in tests)
writes each mode's and KPI's text. lgcov picks the judge: `code` when a tool or node check
tells the group from the passing traces, `jev` otherwise. Every entry it adds is
`review: pending`, with `evidence` (the trace ids) and a one-line `why`; nothing here
approves, `approve` is the human's yes. Pure: the CLI reads and writes the files.

A trace (JSON): {"id", "nodes", "tools", "failure": {"node", "kind", "note"}}; a trace with
no failure passed.
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol, TypedDict, runtime_checkable

import yaml

from lgcov.taxonomy import VERSION
from lgcov.trace import RunTrace, parse_run


@dataclass(frozen=True)
class Failure:
    node: str
    kind: str  # a short tag for what went wrong; with node, the trace's failure signature
    note: str = ""


@dataclass(frozen=True)
class DraftTrace:
    id: str
    run: RunTrace
    failure: Failure | None = None  # None: the run passed


@dataclass(frozen=True)
class TraceGroup:
    """The failing traces with one signature, and the code check that tells them from the
    passing traces, if one does."""

    node: str
    kind: str
    traces: tuple[DraftTrace, ...]
    check: dict[str, str] | None = None

    @property
    def signature(self) -> str:
        return f"{self.node}: {self.kind}"


@dataclass(frozen=True)
class Proposal:
    """What the client writes: the text. lgcov adds the evidence, the judge and the review."""

    mode_id: str
    description: str
    why: str
    kpi_name: str
    kpi_description: str
    rubric: str


@runtime_checkable
class Drafter(Protocol):
    """The pluggable client: proposes one failure mode and its KPI for a group of traces."""

    def propose(self, description: str, group: TraceGroup) -> Proposal: ...


class Doc(TypedDict):
    """A taxonomy file as YAML. Entries stay plain mappings, so a re-draft keeps them as is."""

    version: int
    system: str
    failure_modes: list[dict[str, object]]
    kpis: list[dict[str, object]]


def parse_trace(raw: object, where: str) -> DraftTrace:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
        raise ValueError(f"{where}: a trace needs an 'id'")
    try:
        run = parse_run(raw)
    except ValueError as exc:
        raise ValueError(f"{where}: {exc}") from None
    failure = raw.get("failure")
    if failure is None:
        return DraftTrace(raw["id"], run)
    node, kind = (failure.get(k) if isinstance(failure, dict) else None for k in ("node", "kind"))
    if not isinstance(node, str) or not node or not isinstance(kind, str) or not kind:
        raise ValueError(f"{where}: a failure needs a 'node' and a 'kind'")
    note = failure.get("note") if isinstance(failure, dict) else None
    return DraftTrace(raw["id"], run, Failure(node, kind, note if isinstance(note, str) else ""))


def parse_traces(raw: object, where: str) -> list[DraftTrace]:
    """A trace file: one trace, or a list of them."""
    return [parse_trace(t, where) for t in (raw if isinstance(raw, list) else [raw])]


def _code_check(
    failing: Sequence[DraftTrace], passing: Sequence[DraftTrace]
) -> dict[str, str] | None:
    """A check every passing trace passes and every failing one fails, so code can decide."""
    if not passing:
        return None
    for tool in sorted(set.intersection(*(set(t.run.tools) for t in passing))):
        if all(tool not in t.run.tools for t in failing):
            return {"type": "tool_called", "tool": tool}
    for tool in sorted(set.intersection(*(set(t.run.tools) for t in failing))):
        if all(tool not in t.run.tools for t in passing):
            return {"type": "tool_not_called", "tool": tool}
    for node in sorted(set.intersection(*(set(t.run.nodes) for t in passing))):
        if all(node not in t.run.nodes for t in failing):
            return {"type": "node_visited", "node": node}
    return None


def group_traces(traces: Sequence[DraftTrace]) -> list[TraceGroup]:
    """The failing traces by exact signature, in signature order. No fuzzier clustering."""
    passing = [t for t in traces if t.failure is None]
    by_signature: dict[tuple[str, str], list[DraftTrace]] = {}
    for t in traces:
        if t.failure is not None:
            by_signature.setdefault((t.failure.node, t.failure.kind), []).append(t)
    return [
        TraceGroup(node, kind, tuple(members), _code_check(members, passing))
        for (node, kind), members in sorted(by_signature.items())
    ]


def _judge_why(group: TraceGroup) -> str:
    check = group.check
    if check is None:
        return "jev: no tool or node check tells these traces from the passing ones"
    if check["type"] == "tool_called":
        return f"code: every passing trace calls {check['tool']} and none of these does"
    if check["type"] == "tool_not_called":
        return f"code: each of these calls {check['tool']} and no passing trace does"
    return f"code: every passing trace visits {check['node']} and none of these does"


def _unique(name: str, taken: set[str]) -> str:
    unique, n = name, 1
    while unique in taken:
        n += 1
        unique = f"{name}_{n}"
    taken.add(unique)
    return unique


def draft(
    existing: Doc | None,
    system: str,
    description: str,
    traces: Sequence[DraftTrace],
    client: Drafter,
) -> Doc:
    """`existing` plus one pending failure mode and KPI per failure signature it doesn't have
    yet. Every entry already there is kept as it is, approved or not."""
    doc: Doc = (
        deepcopy(existing)
        if existing
        else {"version": VERSION, "system": system, "failure_modes": [], "kpis": []}
    )
    drafted = {m.get("signature") for m in doc["failure_modes"]}
    ids = {str(m.get("id")) for m in doc["failure_modes"]}
    names = {str(k.get("name")) for k in doc["kpis"]}
    for group in group_traces(traces):
        if group.signature in drafted:
            continue
        p = client.propose(description, group)
        evidence = [t.id for t in group.traces]
        mode_id = _unique(p.mode_id, ids)
        doc["failure_modes"].append(
            {
                "id": mode_id,
                "description": p.description,
                "evidence": evidence,
                "why": p.why,
                "signature": group.signature,
                "review": "pending",
            }
        )
        kpi: dict[str, object] = {
            "name": _unique(p.kpi_name, names),
            "failure_mode": mode_id,
            "node": group.node,
            "judge": "jev" if group.check is None else "code",
            "description": p.kpi_description,
            "rubric": p.rubric,
        }
        if group.check is not None:
            kpi["check"] = dict(group.check)
        kpi |= {"evidence": list(evidence), "why": _judge_why(group), "review": "pending"}
        doc["kpis"].append(kpi)
    return doc


def approve(doc: Doc, entry: str, reviewer: str) -> Doc:
    """A failure mode id approves it and its pending KPIs; a KPI name approves that KPI.
    Records who did. The doc it is given is unchanged."""
    out = deepcopy(doc)
    hits = [m for m in out["failure_modes"] if m.get("id") == entry]
    if hits:
        kpis = out["kpis"]
        hits += [k for k in kpis if k.get("failure_mode") == entry and k.get("review") == "pending"]
    else:
        hits = [k for k in out["kpis"] if k.get("name") == entry]
    if not hits:
        raise ValueError(f"no entry `{entry}`: name a failure mode id or a KPI name")
    for e in hits:
        e["review"], e["reviewer"] = "approved", reviewer
    return out


def as_doc(raw: object, where: str) -> Doc:
    """A taxonomy file already on disk, to draft into or approve in."""
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: a taxonomy must be a mapping")
    modes, kpis, version = raw.get("failure_modes"), raw.get("kpis"), raw.get("version")
    if not isinstance(modes, list) or not isinstance(kpis, list):
        raise ValueError(f"{where}: 'failure_modes' and 'kpis' must be lists")
    if not all(isinstance(e, dict) for e in [*modes, *kpis]):
        raise ValueError(f"{where}: every failure mode and KPI must be a mapping")
    if version != VERSION:
        raise ValueError(f"{where}: unsupported version {version!r}, expected {VERSION}")
    return {
        "version": VERSION,
        "system": str(raw.get("system", "")),
        "failure_modes": modes,
        "kpis": kpis,
    }


HEADER = """\
# Drafted by `lgcov taxonomy draft`. Each entry stays review: pending until a human approves
# it (lgcov taxonomy approve <id> --reviewer <name>); until then `lgcov taxonomy check`
# refuses this file and nothing in it can gate a PR.
"""


def to_yaml(doc: Doc) -> str:
    return HEADER + yaml.safe_dump(dict(doc), sort_keys=False, allow_unicode=True, width=100)
