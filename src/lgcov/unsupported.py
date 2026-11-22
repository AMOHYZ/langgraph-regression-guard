"""T133: patterns lgcov cannot attribute safely return `Unsupported(reason)`, never a
root/propagated label and never a crash."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from lgcov.attribution import GRAPH_NOT_GIVEN, UNEXPLAINED, NodeLabels
from lgcov.fingerprint import fingerprint


class _Named(Protocol):
    @property
    def node(self) -> str: ...


@dataclass(frozen=True)
class Unsupported:
    """A run (or case) whose drift cannot be attributed safely."""

    reason: str
    pattern: str = ""  # occurrence | graph_shape | rename | reducer


def visit_counts(steps: Sequence[_Named]) -> dict[str, int]:
    """How many times each node appears in `steps` (occurrence pairing's denominator)."""
    counts: dict[str, int] = {}
    for step in steps:
        counts[step.node] = counts.get(step.node, 0) + 1
    return counts


def channel_fingerprints(graph: Any) -> dict[str, str]:
    """Stable ids for a compiled graph's state channels (type + reducer), for case 3."""
    channels = getattr(graph, "channels", None)
    if not isinstance(channels, Mapping):
        return {}
    out: dict[str, str] = {}
    for name, channel in sorted(channels.items()):
        key = str(name)
        if key.startswith(("__", "branch:")):
            continue
        kind = type(channel).__name__
        op = getattr(channel, "operator", None)
        out[key] = f"{kind}:{_operator_id(op)}"
    return out


def _operator_id(op: object) -> str:
    if op is None:
        return ""
    if callable(op):
        held = fingerprint(op)
        if held:
            return held
    module = getattr(op, "__module__", "") or ""
    qual = getattr(op, "__qualname__", None) or getattr(op, "__name__", None) or repr(op)
    return f"{module}:{qual}"


def _mismatch(base: Mapping[str, int], fresh: Mapping[str, int]) -> tuple[str, int, int] | None:
    """True multi-visit mismatch: both sides ran the node, but not the same number of times.
    0 vs N is a route that stopped or started reaching the node (T238), not occurrence pairing."""
    for node in sorted(set(base) | set(fresh)):
        left, right = base.get(node, 0), fresh.get(node, 0)
        if left != right and left > 0 and right > 0:
            return node, right, left
    return None


def _empty(labels: NodeLabels) -> NodeLabels:
    return NodeLabels((), (), {}, dict(labels.routing), dict(labels.parts))


def _graph_missing_reasons(labels: NodeLabels) -> frozenset[str]:
    known = frozenset(GRAPH_NOT_GIVEN.values())
    return frozenset(
        n
        for n, why in labels.inconclusive.items()
        if why in known
        or why.startswith("only the PR ran it")
        or why.startswith("only base ran it")
    )


def guard_labels(
    labels: NodeLabels,
    graphs: tuple[Collection[str] | None, Collection[str] | None] = (None, None),
    counts: tuple[Mapping[str, int], Mapping[str, int]] | None = None,
    channels: tuple[Mapping[str, str] | None, Mapping[str, str] | None] = (None, None),
) -> tuple[NodeLabels, Unsupported | None]:
    """T133: drop unsafe root/propagated/inconclusive labels and return `Unsupported` instead.

    Case 1 — occurrence: visit counts differ while labels exist. Case 2 — missing/untrusted
    graph shape, or add+delete that looks like a rename; clear add/delete with both graphs
    trusted stays T238 root. Case 3 — state channel / reducer fingerprints differ and the
    label is UNEXPLAINED (or only propagated with no root).
    """
    empty = _empty(labels)
    labelled = bool(labels.root or labels.propagated or labels.inconclusive)

    if counts is not None and labelled:
        hit = _mismatch(counts[0], counts[1])
        if hit is not None:
            node, fresh, base = hit
            return empty, Unsupported(
                f"occurrence pairing is unsafe: node {node!r} ran {fresh}x, baseline has {base}x",
                "occurrence",
            )

    missing_nodes = _graph_missing_reasons(labels)
    if missing_nodes:
        # Drop only the nodes whose label depended on a missing graph; keep other roots.
        kept = NodeLabels(
            tuple(n for n in labels.root if n not in missing_nodes),
            tuple(n for n in labels.propagated if n not in missing_nodes),
            {n: why for n, why in labels.inconclusive.items() if n not in missing_nodes},
            {n: k for n, k in labels.routing.items() if n not in missing_nodes},
            {n: p for n, p in labels.parts.items() if n not in missing_nodes},
        )
        return kept, Unsupported(
            "graph shape missing or untrusted: an added or removed node can't be "
            "told from one a run didn't reach",
            "graph_shape",
        )

    base_g, head_g = graphs
    if base_g is not None and head_g is not None:
        removed = frozenset(base_g) - frozenset(head_g)
        added = frozenset(head_g) - frozenset(base_g)
        sided = (set(labels.root) | set(labels.propagated) | set(labels.inconclusive)) & (
            removed | added
        )
        if removed and added and sided:
            kept = NodeLabels(
                tuple(n for n in labels.root if n not in sided),
                tuple(n for n in labels.propagated if n not in sided),
                {n: why for n, why in labels.inconclusive.items() if n not in sided},
                {n: k for n, k in labels.routing.items() if n not in sided},
                {n: p for n, p in labels.parts.items() if n not in sided},
            )
            return kept, Unsupported(
                "looks like a rename: nodes removed "
                f"{sorted(removed)} and added {sorted(added)}; not attributed as root",
                "rename",
            )

    # Case 3: only when both shapes are known and equal. Unknown base shape (no cassette
    # `graph`) can pair base's live channels with a different recording; differing shapes are
    # case 2. Same nodes + different channel fingerprints is a real schema/reducer shift.
    base_ch, head_ch = channels
    same_shape = (
        base_g is not None and head_g is not None and frozenset(base_g) == frozenset(head_g)
    )
    if (
        same_shape
        and base_ch is not None
        and head_ch is not None
        and base_ch != head_ch
        and labelled
    ):
        changed = sorted(k for k in set(base_ch) | set(head_ch) if base_ch.get(k) != head_ch.get(k))
        unexplained = {n for n, why in labels.inconclusive.items() if why == UNEXPLAINED}
        schema_wrong = bool(unexplained) or (
            bool(labels.propagated) and not labels.root and not labels.routing
        )
        if schema_wrong:
            channel = changed[0] if changed else "state"
            return empty, Unsupported(
                f"state schema or reducer changed (channel {channel}): cause is outside node "
                "fingerprints",
                "reducer",
            )

    return labels, None
