"""T101a: the attribution benchmark. Does lgcov name the node a change broke (root) and the
nodes that only inherited it (propagated)? Scoring and the report are pure; `lgcov bench`
in cli.py records each benchmark on base, replays head, and hands the labels here.

A benchmark file (YAML): `graph: <file.py>:<attr>`; base and head as `base_patch` and
`head_patch` (whole graph files, for fixtures) or `base_ref` and `head_ref` (git refs in
`repo`, default the file's folder); `cases` (graph inputs); `expected: {root, propagated}`
(node names, a subgraph's inner nodes as `<subgraph node>.<inner node>`); `notes`; and
`hard_cases` from HARD_CASES.

T100a, for real rows: `status: labels_pending_human_review` (listed, never run or scored,
left out of micro and macro), or T100's `status: labels_reviewed` (a human confirmed the
labels, but the row can't run yet: listed the same way, shown apart); `changed_nodes`
(each a diff change: `file`, `change`, the graph `nodes` it belongs to); and `evidence`,
the diff lines behind each label (`node`, `label`, `at` = <file>:<line> of the first + line
on head, `diff`, an optional `why`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import fmean
from typing import Literal

HARD_CASES = ("send_fanout", "subgraph", "loop")
Labels = Literal["root", "propagated"]
PENDING_REVIEW = "labels_pending_human_review"  # T100a: labels a human hasn't checked
LABELS_REVIEWED = "labels_reviewed"  # T100: labels confirmed, the row can't run yet
STATUSES = (PENDING_REVIEW, LABELS_REVIEWED)  # no status: labels confirmed, and it runs


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
    pythonpath: tuple[str, ...] = ()  # T166: folders to import from, inside each side's root
    notes: str = ""
    hard_cases: tuple[str, ...] = ()
    status: str = ""  # one of STATUSES, or "" once the labels are confirmed and it can run
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


def _pythonpath(value: object) -> tuple[str, ...]:
    # T166: a folder outside the side would give both sides one copy of its modules.
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("'pythonpath' must be a list of folders inside the side's root")
    for v in value:
        if Path(v).is_absolute() or ".." in Path(v).parts:
            raise ValueError(f"'pythonpath' must stay inside the side's root, got {v!r}")
    return tuple(value)


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
        pythonpath=_pythonpath(raw.get("pythonpath")),
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


# T162: why a node whose code changed and that drifted is neither root nor propagated.
NEEDS_LIVE = "needs live probe; noise threshold pending T211"
NOT_PROBED = "not probed: scored from saved runs"
NO_FINGERPRINT = "no fingerprint: a subgraph or the like (T193)"
UNEXPLAINED = (
    "drifted with no changed node before it: a change outside node code, an edited recording, "
    "or a live call's variation"
)
ROUTING = "routing change at {}"  # T175: why a node whose own output didn't drift is root


# T211: what a node under its noise threshold reads as. Never "cleared": a one-number regression
# in a long report sits inside the spread, and the KPI judge decides that, not this check.
NO_DRIFT_ABOVE_NOISE = "no drift detected above noise (K={})"
DRIFT_ABOVE_NOISE = "drift above noise (K={})"
Distance = Callable[[object, object], float]


def _words(value: object) -> frozenset[str]:
    return frozenset(json.dumps(value, sort_keys=True, default=str).split())


def word_jaccard(a: object, b: object) -> float:
    """T211: 0 for equal outputs, else how little their word sets overlap, up to 1. Never 0 for
    unequal ones: the extra 1 below counts the difference a word set doesn't show, like order."""
    if a == b:
        return 0.0
    first, second = _words(a), _words(b)
    return 1 - len(first & second) / (len(first | second) + 1)


@dataclass(frozen=True)
class NoiseCheck:
    """T211: the evidence behind one node's verdict. `k`: head's samples."""

    k: int
    spread: float  # the largest distance between two of base's samples: the node's A/A spread
    min_head_to_base: float

    @property
    def drifted(self) -> bool:
        return self.min_head_to_base > self.spread

    @property
    def verdict(self) -> str:
        return (DRIFT_ABOVE_NOISE if self.drifted else NO_DRIFT_ABOVE_NOISE).format(self.k)


def noise_check(
    base: Sequence[object], head: Sequence[object], distance: Distance = word_jaccard
) -> NoiseCheck:
    """T211: `base`: the node's recorded output and base's code run again on the same input;
    `head`: head's code on that input. Drift needs head's nearest sample to be further from base
    than base's samples are from each other, so it misses a shift smaller than the spread. One
    base sample has no spread: any difference is drift, as for a node that repeats exactly."""
    spread = max((distance(a, b) for i, a in enumerate(base) for b in base[i + 1 :]), default=0.0)
    return NoiseCheck(len(head), spread, min(distance(a, b) for a in base for b in head))


def drift_exceeds_noise(
    base: Sequence[object], head: Sequence[object], distance: Distance = word_jaccard
) -> bool:
    """T162, T211: do head's samples of a node differ from base's by more than its own noise?"""
    return noise_check(base, head, distance).drifted


@dataclass(frozen=True)
class Probe:
    """T162: a node run with head's code on the input base recorded for it. `drifted` is None
    when it couldn't run offline, and `reason` says why."""

    drifted: bool | None
    reason: str = ""


@dataclass(frozen=True)
class NodeLabels:
    root: tuple[str, ...]
    propagated: tuple[str, ...]
    inconclusive: dict[str, str]  # node: why
    # T175: the roots that are a changed router's source node, with the runs each is root of.
    routing: dict[str, int] = field(default_factory=dict)


def classify(
    drifted: Collection[str],
    codes: tuple[Mapping[str, str | None], Mapping[str, str | None]],
    probes: Mapping[str, Probe],
) -> NodeLabels:
    """T162: each drifted node is root when its fingerprint changed (base's code against head's)
    and it drifts on base's recorded input (`probes`), propagated when it only drifts because its
    input did. A node only one side ran is root: the PR added or removed it. T212: a comment, a
    reformat or the node function's own docstring doesn't change a fingerprint."""
    base, head = codes
    root: list[str] = []
    propagated: list[str] = []
    inconclusive: dict[str, str] = {}
    for node in sorted(set(drifted)):
        probe = probes.get(node, Probe(None, NOT_PROBED))
        if node not in base or node not in head:
            root.append(node)
        elif base[node] is None or head[node] is None:
            inconclusive[node] = NO_FINGERPRINT
        elif base[node] == head[node] or probe.drifted is False:
            propagated.append(node)
        elif probe.drifted:
            root.append(node)
        else:
            inconclusive[node] = probe.reason
    return NodeLabels(tuple(root), tuple(propagated), inconclusive)


def explain_runs(
    labels: NodeLabels,
    first_drifts: Sequence[str | None],
    drifted: Sequence[Collection[str]],
    routers: tuple[Mapping[str, str | None], Mapping[str, str | None]] = ({}, {}),
    ran: Sequence[Sequence[str]] = (),
    same_input: Sequence[Collection[str]] = (),
) -> NodeLabels:
    """T162: a run whose first drifted node is neither root nor inconclusive has a cause no node's
    fingerprint shows: an edge, a router, a Send, an edited recording, or a live call's variation.
    That node is inconclusive, so `fail-on: root-drift` still fails on it, as first-drift
    attribution did. T230: a root or inconclusive node after it explains nothing about it.

    T230: propagated means the node's input differed. A node labelled propagated that drifted in a
    run where both sides gave it the same input (`same_input`, by run) is inconclusive too,
    wherever it ran: in a parallel branch it can come after an unrelated root. Empty when the
    inputs aren't known, which leaves the first-drift rule.

    T175: unless a router explains it. A node whose router fingerprint changed (`routers`: base's
    and head's, by source node) is root of each run where a node drifted after it, by base's order
    (`ran`), beside any root the run already has. A router with no fingerprint on either side, as
    in a recording from before they were kept, explains nothing. A router that ran after the
    run's first drifted node doesn't explain that node. Not checked: that the run took another
    branch. A router edit that routes as before is root of drift a changed node caused."""
    base, head = routers
    edited = {n for n, code in base.items() if code and head.get(n) and head[n] != code}
    unsure = dict(labels.inconclusive)
    routing: dict[str, int] = {}
    for i, (first, nodes) in enumerate(zip(first_drifts, drifted, strict=True)):
        order = list(ran[i]) if i < len(ran) else []
        after = {r for r in edited & set(order) if set(nodes) - set(order[: order.index(r) + 1])}
        for router in after:
            routing[router] = routing.get(router, 0) + 1
        reached = order[: order.index(first) + 1] if first in order else order
        if first and not after & set(reached) and first not in {*labels.root, *unsure}:
            unsure[first] = UNEXPLAINED
        same = set(same_input[i]) if i < len(same_input) else set()
        for node in same & set(nodes) & set(labels.propagated):
            unsure[node] = UNEXPLAINED
    propagated = tuple(n for n in labels.propagated if n not in unsure)
    root = tuple(sorted({*labels.root, *routing}))
    return NodeLabels(root, propagated, unsure, dict(sorted(routing.items())))


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
    status: str = ""  # one of STATUSES: listed, never run or scored
    first_drift: tuple[str, ...] = ()  # T162: the first node to drift in some run
    inconclusive: tuple[tuple[str, str], ...] = ()  # T162: (node, why), sorted
    noise: tuple[tuple[str, NoiseCheck], ...] = ()  # T211: each repeated node's evidence, sorted

    @property
    def pending(self) -> bool:
        """Not run because of its `status` (labels pending review, or reviewed but not runnable)."""
        return bool(self.status)

    @property
    def ok(self) -> bool:
        return (
            not self.error
            and not (self.root.fp or self.root.fn)
            and not (self.propagated.fp or self.propagated.fn)
        )


def pending_result(b: Benchmark) -> Result:
    """T100a: the row for a benchmark with a `status`: never run, no scores."""
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
    if r.status == LABELS_REVIEWED:
        return "📝 labels reviewed, not run"
    if r.pending:
        return "⏳ pending review"
    if r.error:
        return f"❌ run error: {r.error}"
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
        if r.pending or r.error:  # not scored: an errored row's zero Score would read as 1.00
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
    pending = sum(r.status == PENDING_REVIEW for r in results)
    if pending:
        lines += [
            "",
            f"⏳ {pending} pending human review: listed, not run, and left out of micro and macro.",
        ]
    reviewed = sum(r.status == LABELS_REVIEWED for r in results)
    if reviewed:
        lines += [
            "",
            f"📝 {reviewed} with labels reviewed, not yet runnable: listed, not run, and left out"
            " of micro and macro.",
        ]
    gaps = [r for r in results if not r.ok and not r.pending]
    if gaps:
        lines += ["", "## Known gaps", ""]
        for r in gaps:
            kinds = f" ({', '.join(r.hard_cases)})" if r.hard_cases else ""
            if r.error:
                lines.append(f"- `{r.name}`{kinds}: run error: {r.error}")
                continue
            unsure = "; ".join(f"`{n}` ({why})" for n, why in r.inconclusive)
            lines.append(
                f"- `{r.name}`{kinds}: lgcov said root {_nodes(r.found_root)}, expected "
                f"{_nodes(r.expected_root)}."
                + (f" Inconclusive: {unsure}." if unsure else "")
                + (f" {r.notes}" if r.notes else "")
            )
    if any(r.noise for r in results):  # T211: the evidence, whichever way each check went
        lines += ["", "## Noise checks", ""]
        lines += [
            f"- `{r.name}`: `{n}`: {c.verdict}: spread {c.spread:.3f}, nearest head sample "
            f"{c.min_head_to_base:.3f}"
            for r in results
            for n, c in r.noise
        ]
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
                "first_drift": list(r.first_drift),
                "inconclusive": dict(r.inconclusive),
                "noise": {n: {**asdict(c), "verdict": c.verdict} for n, c in r.noise},
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
