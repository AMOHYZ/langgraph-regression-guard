"""T120: proposal-mode JSON and prompt overlay (pure). `lgcov propose` prints this."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import FunctionType
from typing import Any, Literal

from lgcov.sampling import KpiStats
from lgcov.stats import Decision, ni_required_n, no_drop_cases

ProposeState = Literal["eligible", "blocked", "needs_human", "infra", "unsupported"]
PROMPT_ATTRS: tuple[str, ...] = ("system_prompt", "prompt")
PROMPT_PART_SUFFIXES: tuple[str, ...] = (".system_prompt", ".prompt")
RECORD_HINT = "Run `lgcov guard --record` to record this candidate on the ledger."


@dataclass(frozen=True)
class DeclaredPrompt:
    """Where a node's prompt is declared: a named attribute or a recording part, never scraped."""

    node: str
    attr: str
    value: str
    source: str  # "attribute" | "part"


@dataclass(frozen=True)
class ProposeKpi:
    name: str
    verdict: Decision
    delta_hat: float | None
    ci: tuple[float, float] | None
    mde: float | None
    n_star: int | None
    ni_n_star: int | None
    q: float | None
    p_adj: float | None


@dataclass(frozen=True)
class ProposeNode:
    id: str
    label: Literal["root", "propagated"]


@dataclass(frozen=True)
class ProposeReport:
    kpis: tuple[ProposeKpi, ...]
    nodes: tuple[ProposeNode, ...]
    state: str
    reasons: tuple[str, ...]
    exposure_recorded: bool = False
    k_gate: int = 1


def cap_non_blocking_judges(verdict: str, judge: str, uncalibrated: bool = False) -> Decision:
    """FakeJudge and uncalibrated model judges never BLOCK a proposal."""
    if verdict == "BLOCK" and (judge == "fake" or uncalibrated):
        return "INCONCLUSIVE"
    if verdict == "SHIP":
        return "SHIP"
    if verdict == "BLOCK":
        return "BLOCK"
    return "INCONCLUSIVE"


def _node_fn(graph: object, node: str) -> FunctionType | None:
    spec = getattr(graph, "nodes", {}).get(node)
    bound = getattr(spec, "bound", None)
    fn = getattr(bound, "func", None) or getattr(bound, "afunc", None)
    return fn if isinstance(fn, FunctionType) else None


def _prompt_attr(obj: object) -> tuple[str, str] | None:
    for attr in PROMPT_ATTRS:
        value = getattr(obj, attr, None)
        if isinstance(value, str) and value:
            return attr, value
    return None


def declared_prompt(
    graph: object, node: str, parts: Mapping[str, Mapping[str, object]] | None = None
) -> DeclaredPrompt | None:
    """A prompt this node declares, or None. Never scrapes strings from the function body.

    1. A recording `parts` / live chat-model field whose name is a prompt (`*.prompt`,
       `*.system_prompt`) on this node.
    2. Else a well-known prompt attribute on the node function (`system_prompt`, `prompt`).
    3. Else None — `node has no declared prompt`.
    """
    fn = _node_fn(graph, node)
    if fn is not None:
        found = _prompt_attr(fn)
        if found is not None:
            return DeclaredPrompt(node, found[0], found[1], "attribute")
        for cell in fn.__closure__ or ():
            found = _prompt_attr(cell.cell_contents)
            if found is not None:
                return DeclaredPrompt(node, found[0], found[1], "attribute")
        for name in fn.__code__.co_names:
            obj = fn.__globals__.get(name)
            if obj is fn or obj is None:
                continue
            found = _prompt_attr(obj)
            if found is not None:
                return DeclaredPrompt(node, found[0], found[1], "attribute")
    for key in parts or ():
        if not key.startswith(f"{node}.") or not key.endswith(PROMPT_PART_SUFFIXES):
            continue
        return DeclaredPrompt(node, key.rsplit(".", 1)[-1], "", "part")
    return None


def apply_declared_prompt(source: str, found: DeclaredPrompt, new_prompt: str) -> str:
    """Replace the declared prompt's current value in source. ValueError if it isn't there."""
    if not found.value or found.value not in source:
        raise ValueError(f"node {found.node} has no declared prompt in source")
    return source.replace(found.value, new_prompt.rstrip("\n"), 1)


def resolve_state(
    *,
    statistical: Decision | None,
    drift_roots: Sequence[str],
    overlay: bool,
    locked: bool,
    pools_edited: bool,
    unsupported: Sequence[str],
    infra: str,
    fail_on: str = "root-drift",
    kpi_block: bool = False,
) -> tuple[str, tuple[str, ...]]:
    """The rule that sets proposal state. Overlay is capped at needs_human (never eligible)."""
    if infra:
        return "infra", (infra,)
    if unsupported:
        return "unsupported", tuple(unsupported)
    if pools_edited:
        return "needs_human", ("pool violation",)
    reasons: list[str] = []
    if fail_on in ("root-drift", "any-drift") and drift_roots:
        reasons.extend(f"root drift on {node}" for node in drift_roots)
        if overlay:
            reasons.append("candidate not locked")
            return "needs_human", tuple(reasons)
        return "blocked", tuple(reasons)
    if kpi_block or statistical == "BLOCK":
        if overlay:
            return "needs_human", ("KPI BLOCK", "candidate not locked")
        return "blocked", ("KPI BLOCK",)
    if statistical == "INCONCLUSIVE":
        reasons = ["inconclusive KPI"]
        if overlay:
            reasons.append("candidate not locked")
        return "needs_human", tuple(reasons)
    if overlay or not locked:
        return "needs_human", ("candidate not locked",)
    if statistical == "SHIP" and not drift_roots:
        return "eligible", ("SHIP",)
    return "needs_human", ("needs human review",)


def kpi_from_stats(s: KpiStats, judge: str, uncalibrated: bool, alpha: float = 0.05) -> ProposeKpi:
    raw = s.decision or "INCONCLUSIVE"
    verdict = cap_non_blocking_judges(raw, judge, uncalibrated)
    paired = s.paired
    if paired is None:
        return ProposeKpi(s.kpi, verdict, None, None, None, None, None, None, s.p_adjusted)
    n_star = paired.required_n
    if paired.discordant == 0:
        n_star = no_drop_cases(s.margin, alpha, s.score_range)
    ni: int | None = None
    if paired.rho is not None:
        ni = ni_required_n(paired.base_rate, paired.new_rate, paired.rho, s.margin, alpha)
    q = (paired.n / n_star) if n_star else None
    return ProposeKpi(
        s.kpi,
        verdict,
        paired.delta,
        paired.ci,
        paired.mde,
        n_star,
        ni,
        q,
        s.p_adjusted,
    )


def report_to_json(report: ProposeReport) -> str:
    def kpi(row: ProposeKpi) -> dict[str, object]:
        return {
            "name": row.name,
            "verdict": row.verdict,
            "delta_hat": row.delta_hat,
            "ci": list(row.ci) if row.ci is not None else None,
            "mde": row.mde,
            "n_star": row.n_star,
            "ni_n_star": row.ni_n_star,
            "q": row.q,
            "p_adj": row.p_adj,
        }

    doc = {
        "kpis": [kpi(row) for row in report.kpis],
        "nodes": [{"id": n.id, "label": n.label} for n in report.nodes],
        "state": report.state,
        "reasons": list(report.reasons),
        "exposure_recorded": False,
        "k_gate": report.k_gate,
    }
    return json_dumps(doc)


def json_dumps(doc: object) -> str:
    return json.dumps(doc, indent=2) + "\n"


def validate_against_schema(instance: object, schema: Mapping[str, Any], path: str = "$") -> None:
    """Tiny JSON Schema checker (no extra dependency): type, required, properties, enum, items."""
    errors = _schema_errors(instance, schema, path)
    if errors:
        raise ValueError("; ".join(errors))


def _schema_errors(instance: object, schema: Mapping[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(instance, dict):
            return [f"{path}: expected object"]
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if key not in instance:
                    errors.append(f"{path}: missing {key}")
        props = schema.get("properties")
        if isinstance(props, dict):
            for key, sub in props.items():
                if key in instance and isinstance(sub, dict):
                    errors.extend(_schema_errors(instance[key], sub, f"{path}.{key}"))
    elif expected == "array":
        if not isinstance(instance, list):
            return [f"{path}: expected array"]
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(instance):
                errors.extend(_schema_errors(item, items, f"{path}[{i}]"))
    elif expected == "string":
        if not isinstance(instance, str):
            errors.append(f"{path}: expected string")
    elif expected == "number":
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            errors.append(f"{path}: expected number")
    elif expected == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            errors.append(f"{path}: expected integer")
    elif expected == "boolean":
        if not isinstance(instance, bool):
            errors.append(f"{path}: expected boolean")
    elif expected == "null":
        if instance is not None:
            errors.append(f"{path}: expected null")
    types = schema.get("type")
    if isinstance(types, list):
        if not any(not _schema_errors(instance, {**schema, "type": t}, path) for t in types):
            errors.append(f"{path}: expected one of {types}")
        else:
            errors = []
    enum = schema.get("enum")
    if isinstance(enum, list) and instance not in enum:
        errors.append(f"{path}: {instance!r} not in {enum}")
    return errors
