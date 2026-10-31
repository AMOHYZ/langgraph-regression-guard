"""T101a: the attribution benchmark. Does lgcov name the node a change broke (root) and the
nodes that only inherited it (propagated)? Scoring and the report are pure; `lgcov bench`
in cli.py records each benchmark on base, replays head, and hands the labels here.

A benchmark file (YAML): `graph: <file.py>:<attr>`; base and head as `base_patch` and
`head_patch` (whole graph files, for fixtures) or `base_ref` and `head_ref` (git refs in
`repo`, default the file's folder); `cases` (graph inputs); `expected: {root, propagated}`
(node names, a subgraph's inner nodes as `<subgraph node>.<inner node>`); `notes`; and
`hard_cases` from HARD_CASES.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Literal

HARD_CASES = ("send_fanout", "subgraph", "loop")
Labels = Literal["root", "propagated"]


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


def _names(value: object, where: str) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{where} must be a list of node names")
    return frozenset(value)


def _text(raw: Mapping[object, object], key: str) -> str | None:
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"'{key}' must be a string")
    return value


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
    return Benchmark(
        name=name,
        graph=graph,
        cases=tuple(cases),
        root=_names(expected.get("root"), "expected.root"),
        propagated=_names(expected.get("propagated"), "expected.propagated"),
        folder=folder,
        base_patch=patches[0],
        head_patch=patches[1],
        base_ref=refs[0],
        head_ref=refs[1],
        repo=_text(raw, "repo") or ".",
        notes=_text(raw, "notes") or "",
        hard_cases=tuple(str(h) for h in hard),
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

    @property
    def ok(self) -> bool:
        return (
            not self.error
            and not (self.root.fp or self.root.fn)
            and not (self.propagated.fp or self.propagated.fn)
        )


def micro(results: Sequence[Result], which: Labels) -> Score:
    """Every label of every benchmark counted together; errored benchmarks left out."""
    scores = [getattr(r, which) for r in results if not r.error]
    return Score(sum(s.tp for s in scores), sum(s.fp for s in scores), sum(s.fn for s in scores))


def macro(results: Sequence[Result], which: Labels) -> tuple[float, float, float]:
    """The mean of each benchmark's precision, recall and F1."""
    scores: list[Score] = [getattr(r, which) for r in results if not r.error]
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
        lines.append(
            f"| {r.name} | {_prf(r.root.precision, r.root.recall, r.root.f1)} "
            f"| {_prf(r.propagated.precision, r.propagated.recall, r.propagated.f1)} "
            f"| {_nodes(r.found_root)} | {_nodes(r.expected_root)} | {r.seconds:.2f} s "
            f"| {_status(r)} |"
        )
    root, prop = micro(results, "root"), micro(results, "propagated")
    lines += [
        f"| **micro** | {_prf(root.precision, root.recall, root.f1)} "
        f"| {_prf(prop.precision, prop.recall, prop.f1)} | | | | |",
        f"| **macro** | {_prf(*macro(results, 'root'))} | {_prf(*macro(results, 'propagated'))} "
        "| | | | |",
    ]
    gaps = [r for r in results if not r.ok]
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
