"""T101a: the attribution benchmark. Does lgcov name the node a change broke (root) and the
nodes that only inherited it (propagated)? Scoring and the report are pure; `lgcov bench`
in cli.py records each benchmark on base, replays head, and hands the labels here.

A benchmark file (YAML): `graph: <file.py>:<attr>`; base and head as `base_patch` and
`head_patch` (whole graph files, for fixtures) or `base_ref` and `head_ref` (git refs in
`repo`, default the file's folder); `cases` (graph inputs); `expected: {root, propagated}`
(node names, a subgraph's inner nodes as `<subgraph node>.<inner node>`); `notes`; and
`hard_cases` from HARD_CASES.

T100a, for real rows: `status: labels_pending_human_review` (listed, never run or scored,
left out of micro and macro); `changed_nodes` (each a diff change: `file`, `change`, the
graph `nodes` it belongs to); and `evidence`, the diff lines behind each label (`node`,
`label`, `at` = <file>:<line> of the first + line on head, `diff`, an optional `why`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Literal

HARD_CASES = ("send_fanout", "subgraph", "loop")
Labels = Literal["root", "propagated"]
STATUSES = ("labels_pending_human_review",)  # T100a; no status: labels confirmed, it runs


@dataclass(frozen=True)
class Changed:
    """T100a: one change in the PR's diff, and the graph nodes it belongs to."""

    file: str
    change: str
    nodes: tuple[str, ...]


@dataclass(frozen=True)
class Evidence:
    """T100a: diff lines behind a label. `at`: <file>:<line> of the first + line on head."""

    node: str
    label: Labels
    at: str
    diff: str  # verbatim diff lines, each starting with +, - or a space
    why: str = ""


@dataclass(frozen=True)
class Benchmark:
    name: str
    graph: str  # "<file.py>:<attr>", the path relative to the patches' or the repo's root
    cases: tuple[dict[str, object], ...]
    root: frozenset[str]  # expected
    propagated: frozenset[str]
    folder: Path  # the file's folder: patches and repo resolve against it
    base_patch: str | None = None
    head_patch: str | None = None
    base_ref: str | None = None
    head_ref: str | None = None
    repo: str = "."
    notes: str = ""
    hard_cases: tuple[str, ...] = ()
    status: str = ""  # T100a: one of STATUSES, or "" once a human confirmed the labels
    changed_nodes: tuple[Changed, ...] = ()
    evidence: tuple[Evidence, ...] = ()


def _names(value: object, where: str) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{where} must be a list of node names")
    return frozenset(value)


def _text(raw: Mapping[object, object], key: str) -> str | None:
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"'{key}' must be a string")
    return value


def _changed(raw: object) -> tuple[Changed, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(c, dict) for c in raw):
        raise ValueError("'changed_nodes' must be a list of {file, change, nodes}")
    found = []
    for c in raw:
        file, change = _text(c, "file"), _text(c, "change")
        if not file or not change:
            raise ValueError("each of 'changed_nodes' needs a 'file' and a 'change'")
        found.append(Changed(file, change, tuple(sorted(_names(c.get("nodes"), "nodes")))))
    return tuple(found)


def _evidence(
    raw: object, root: frozenset[str], propagated: frozenset[str]
) -> tuple[Evidence, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(e, dict) for e in raw):
        raise ValueError("'evidence' must be a list of {node, label, at, diff}")
    expected: dict[str, frozenset[str]] = {"root": root, "propagated": propagated}
    found = []
    for e in raw:
        node, label, at, diff = (_text(e, k) or "" for k in ("node", "label", "at", "diff"))
        if label not in expected:
            raise ValueError(f"evidence for `{node}`: 'label' must be root or propagated")
        if node not in expected[label]:
            raise ValueError(f"evidence: `{node}` isn't an expected {label}")
        if not re.fullmatch(r"[^\s:]+:\d+", at):
            raise ValueError(f"evidence for `{node}`: 'at' must be <file>:<line>, got {at!r}")
        if not diff or not all(line[:1] in "+- " and line for line in diff.splitlines()):
            raise ValueError(f"evidence for `{node}`: each diff line must start with +, - or ' '")
        typed: Labels = "root" if label == "root" else "propagated"
        found.append(Evidence(node, typed, at, diff, _text(e, "why") or ""))
    if found:  # a row that gives evidence gives it for every label
        backed = {(e.node, e.label) for e in found}
        for label, nodes in expected.items():
            missing = sorted(n for n in nodes if (n, label) not in backed)
            if missing:
                raise ValueError(f"no evidence for {label} {_nodes(missing)}")
    return tuple(found)


def parse_benchmark(name: str, raw: object, folder: Path) -> Benchmark:
    """One benchmark file, validated (pure)."""
    if not isinstance(raw, dict):
        raise ValueError("a benchmark file must be a mapping")
    graph = _text(raw, "graph")
    if not graph or ":" not in graph:
        raise ValueError("'graph' must be '<file.py>:<attr>'")
    patches = (_text(raw, "base_patch"), _text(raw, "head_patch"))
    refs = (_text(raw, "base_ref"), _text(raw, "head_ref"))
    if not all(patches) and not all(refs):
        raise ValueError("needs base_patch and head_patch, or base_ref and head_ref")
    cases = raw.get("cases")
    if not isinstance(cases, list) or not cases or not all(isinstance(c, dict) for c in cases):
        raise ValueError("'cases' must be a non-empty list of graph inputs")
    expected = raw.get("expected")
    if not isinstance(expected, dict):
        raise ValueError("'expected' must map root and propagated to node names")
    hard = raw.get("hard_cases") or []
    if not isinstance(hard, list) or not set(hard) <= set(HARD_CASES):
        raise ValueError(f"'hard_cases' must be a list from {list(HARD_CASES)}")
    status = _text(raw, "status") or ""
    if status and status not in STATUSES:
        raise ValueError(f"'status' must be one of {list(STATUSES)}, or left out, got {status!r}")
    root = _names(expected.get("root"), "expected.root")
    propagated = _names(expected.get("propagated"), "expected.propagated")
    return Benchmark(
        name=name,
        graph=graph,
        cases=tuple(cases),
        root=root,
        propagated=propagated,
        folder=folder,
        base_patch=patches[0],
        head_patch=patches[1],
        base_ref=refs[0],
        head_ref=refs[1],
        repo=_text(raw, "repo") or ".",
        notes=_text(raw, "notes") or "",
        hard_cases=tuple(str(h) for h in hard),
        status=status,
        changed_nodes=_changed(raw.get("changed_nodes")),
        evidence=_evidence(raw.get("evidence"), root, propagated),
    )


@dataclass(frozen=True)
class Score:
    tp: int
    fp: int
    fn: int

    @property
    def precision(self) -> float:
        found = self.tp + self.fp
        return self.tp / found if found else (1.0 if not self.fn else 0.0)

    @property
    def recall(self) -> float:
        expected = self.tp + self.fn
        return self.tp / expected if expected else (1.0 if not self.fp else 0.0)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def score(found: Collection[str], expected: Collection[str]) -> Score:
    hit = set(found) & set(expected)
    return Score(len(hit), len(set(found) - hit), len(set(expected) - hit))


@dataclass(frozen=True)
class Result:
    name: str
    found_root: tuple[str, ...]
    found_propagated: tuple[str, ...]
    root: Score
    propagated: Score
    seconds: float
    expected_root: tuple[str, ...] = ()
    expected_propagated: tuple[str, ...] = ()
    hard_cases: tuple[str, ...] = ()
    notes: str = ""
    error: str = ""  # the benchmark couldn't run: no scores
    status: str = ""  # T100a: labels pending human review: listed, never run or scored

    @property
    def pending(self) -> bool:
        return bool(self.status)

    @property
    def ok(self) -> bool:
        return (
            not self.error
            and not (self.root.fp or self.root.fn)
            and not (self.propagated.fp or self.propagated.fn)
        )


def pending_result(b: Benchmark) -> Result:
    """T100a: the row for a benchmark whose labels wait for a human: never run, no scores."""
    zero = Score(0, 0, 0)
    expected = (tuple(sorted(b.root)), tuple(sorted(b.propagated)))
    return Result(b.name, (), (), zero, zero, 0.0, *expected, b.hard_cases, b.notes, "", b.status)


def _scored(results: Sequence[Result]) -> list[Result]:
    return [r for r in results if not r.error and not r.pending]


def micro(results: Sequence[Result], which: Labels) -> Score:
    """Every label of every benchmark counted together; errored and pending ones left out."""
    scores = [getattr(r, which) for r in _scored(results)]
    return Score(sum(s.tp for s in scores), sum(s.fp for s in scores), sum(s.fn for s in scores))


def macro(results: Sequence[Result], which: Labels) -> tuple[float, float, float]:
    """The mean of each benchmark's precision, recall and F1."""
    scores: list[Score] = [getattr(r, which) for r in _scored(results)]
    if not scores:
        return (0.0, 0.0, 0.0)
    return (
        fmean(s.precision for s in scores),
        fmean(s.recall for s in scores),
        fmean(s.f1 for s in scores),
    )


def _prf(p: float, r: float, f: float) -> str:
    return f"{p:.2f} / {r:.2f} / {f:.2f}"


def _nodes(names: Sequence[str]) -> str:
    return ", ".join(f"`{n}`" for n in names) or "—"


def _status(r: Result) -> str:
    if r.pending:
        return "⏳ pending review"
    if r.error:
        return f"⚠️ didn't run: {r.error}"
    if r.ok:
        return "✓"
    return f"❌ known gap ({', '.join(r.hard_cases)})" if r.hard_cases else "❌"


def render_markdown(results: Sequence[Result], command: str, date: str, source: str = "") -> str:
    """docs/benchmark.md: a row per benchmark, micro and macro rows, the known gaps.
    `source` says where the benchmarks came from."""
    lines = [
        "# Attribution benchmark",
        "",
        f"Run {date}: does lgcov name the node a change broke (root) and the nodes that only "
        "inherited the damage (propagated)? Each benchmark records its cases on base, replays "
        "head, and compares lgcov's labels with hand-written ones"
        + (f": {source}" if source else "")
        + ". Precision / recall / F1:",
        "",
        "| benchmark | root | propagated | found root | expected root | time | |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.pending:
            lines.append(f"| {r.name} | — | — | — | {_nodes(r.expected_root)} | — | {_status(r)} |")
            continue
        lines.append(
            f"| {r.name} | {_prf(r.root.precision, r.root.recall, r.root.f1)} "
            f"| {_prf(r.propagated.precision, r.propagated.recall, r.propagated.f1)} "
            f"| {_nodes(r.found_root)} | {_nodes(r.expected_root)} | {r.seconds:.2f} s "
            f"| {_status(r)} |"
        )
    root, prop = micro(results, "root"), micro(results, "propagated")
    if _scored(results):
        lines += [
            f"| **micro** | {_prf(root.precision, root.recall, root.f1)} "
            f"| {_prf(prop.precision, prop.recall, prop.f1)} | | | | |",
            f"| **macro** | {_prf(*macro(results, 'root'))} "
            f"| {_prf(*macro(results, 'propagated'))} | | | | |",
        ]
    else:  # nothing scored: no aggregate to show
        lines += ["| **micro** | — | — | | | | |", "| **macro** | — | — | | | | |"]
    pending = sum(r.pending for r in results)
    if pending:
        lines += [
            "",
            f"⏳ {pending} pending human review: listed, not run, and left out of micro and macro.",
        ]
    gaps = [r for r in results if not r.ok and not r.pending]
    if gaps:
        lines += ["", "## Known gaps", ""]
        for r in gaps:
            kinds = f" ({', '.join(r.hard_cases)})" if r.hard_cases else ""
            if r.error:
                lines.append(f"- `{r.name}`{kinds}: didn't run: {r.error}")
                continue
            lines.append(
                f"- `{r.name}`{kinds}: lgcov said root {_nodes(r.found_root)}, expected "
                f"{_nodes(r.expected_root)}." + (f" {r.notes}" if r.notes else "")
            )
    lines += ["", "Reproduce:", "", "```bash", command, "```", ""]
    return "\n".join(lines)


def _score_json(s: Score) -> dict[str, float]:
    return {
        "tp": s.tp,
        "fp": s.fp,
        "fn": s.fn,
        "precision": s.precision,
        "recall": s.recall,
        "f1": s.f1,
    }


def results_json(results: Sequence[Result]) -> str:
    """benchmark-results.json: per benchmark, then micro and macro."""
    doc = {
        "benchmarks": [
            {
                "name": r.name,
                "root": _score_json(r.root),
                "propagated": _score_json(r.propagated),
                "found_root": list(r.found_root),
                "found_propagated": list(r.found_propagated),
                "expected_root": list(r.expected_root),
                "expected_propagated": list(r.expected_propagated),
                "seconds": round(r.seconds, 3),
                "hard_cases": list(r.hard_cases),
                "notes": r.notes,
                "error": r.error,
                "status": r.status,
            }
            for r in results
        ],
        "micro": {w: _score_json(micro(results, w)) for w in ("root", "propagated")},
        "macro": {
            w: dict(zip(("precision", "recall", "f1"), macro(results, w), strict=True))
            for w in ("root", "propagated")
        },
    }
    return json.dumps(doc, indent=2) + "\n"
