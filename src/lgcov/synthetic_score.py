"""T121: scoring the synthetic suite. A method names nodes on a case; a vote records what that
got right. The rates are the same sums over cases and over edits. Pure: no runs happen here."""

import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Literal, Protocol, TypedDict

from lgcov.cassette import Cassette
from lgcov.stats import wilson
from lgcov.synthetic import AaResult, Case, NoiseRate, SharedCase

Method = Callable[[Case], set[str]]


@dataclass(frozen=True)
class Found:
    """T222: what `lgcov ci` says about one case's nodes."""

    root: frozenset[str]
    inconclusive: frozenset[str] = frozenset()
    one_sided: frozenset[str] = frozenset()  # roots named because only one side ran the node


Labeller = Callable[[Case], Found]


class Replayed(Protocol):
    """T182: what a method reads off a case, T121's or T182's: base's run, the PR's run of the
    same input, and the edited agent."""

    @property
    def base(self) -> Cassette: ...

    @property
    def pr(self) -> Cassette: ...

    @property
    def graph(self) -> object: ...


@dataclass(frozen=True)
class Vote:
    """One case, or one edit's majority across its questions, under one method."""

    regressed: bool
    activated: bool
    named: bool  # the method named a node
    hit: bool  # one of them was the edited node


@dataclass(frozen=True)
class Rate:
    hits: int
    of: int

    @property
    def value(self) -> float | None:
        return self.hits / self.of if self.of else None  # None, so nothing reads as a 0

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson(self.hits, self.of) if self.of else None


@dataclass(frozen=True)
class Rates:
    precision: Rate  # of what it named, the edited node on a regression
    recall: Rate  # of regressions, the edited node named
    coverage: Rate  # of edits that changed the run, the edited node named
    false_flag: Rate  # of edits that changed the run and did not regress, a node named


def rates(votes: Sequence[Vote]) -> Rates:
    caught = sum(v.regressed and v.hit for v in votes)
    activated = [v for v in votes if v.activated]
    harmless = [v for v in activated if not v.regressed]
    return Rates(
        Rate(caught, sum(v.named for v in votes)),
        Rate(caught, sum(v.regressed for v in votes)),
        Rate(sum(v.hit for v in activated), len(activated)),
        Rate(sum(v.named for v in harmless), len(harmless)),
    )


@dataclass(frozen=True)
class NodeScore:
    """T222: each node named root counted on its own, since the shipped labels can name several."""

    precision: Rate  # of the nodes named root, the edited node of a regressed case
    recall: Rate  # of regressed cases, the edited node named root
    exact: Rate  # of regressed cases, the roots named are exactly the edited node
    inconclusive: int  # cases that name no root and an inconclusive node: misses, not false roots
    one_sided: Rate  # of the nodes named root, named because only one side ran them


def node_score(found: Sequence[tuple[Case, Found]]) -> NodeScore:
    regressed = [(case, f) for case, f in found if case.truth.regressed]
    hits = sum(case.truth.edited_node in f.root for case, f in regressed)
    named = sum(len(f.root) for _, f in found)
    return NodeScore(
        Rate(hits, named),
        Rate(hits, len(regressed)),
        Rate(sum(f.root == {case.truth.edited_node} for case, f in regressed), len(regressed)),
        sum(1 for _, f in found if f.inconclusive and not f.root),
        Rate(sum(len(f.one_sided & f.root) for _, f in found), named),
    )


def by_edit[K](votes: Mapping[K, Sequence[Vote]]) -> list[Vote]:
    """One vote per edit, by majority across its questions. A tie isn't a majority."""

    def most(field: Callable[[Vote], bool], cast: Sequence[Vote]) -> bool:
        return sum(map(field, cast)) * 2 > len(cast)

    return [
        Vote(
            most(lambda v: v.regressed, cast),
            most(lambda v: v.activated, cast),
            most(lambda v: v.named, cast),
            most(lambda v: v.hit, cast),
        )
        for cast in votes.values()
    ]


def votes_for(method: Method, case: Case) -> Vote:
    named = method(case)
    truth = case.truth
    return Vote(truth.regressed, truth.activated, bool(named), truth.edited_node in named)


def first_divergence(case: Replayed) -> set[str]:
    """The first step the run has and the recording doesn't, or else the first it stopped reaching.
    Matched as a multiset, since parallel workers finish in any order."""
    left = Counter((step.node, repr(step.output)) for step in case.base.steps)
    for step in case.pr.steps:
        key = (step.node, repr(step.output))
        if not left[key]:
            return {step.node}
        left[key] -= 1
    stopped = (step.node for step in case.base.steps if left[step.node, repr(step.output)])
    node = next(stopped, None)
    return set() if node is None else {node}


def _edited_node(case: Case) -> set[str]:
    return {case.truth.edited_node}


def _no_node(case: Case) -> set[str]:
    return set()


FIRST_DRIFT = "first drift + router rule"  # T222: what "lgcov root" scored before
METHODS: dict[str, Method] = {
    "edited node from the diff": _edited_node,
    "first divergence": first_divergence,
    "end-to-end": _no_node,  # flags a run, names no node: end_to_end_flags
}


def end_to_end_flags(case: Replayed) -> bool:
    """The final answer changed: what a test on only the agent's output would see."""
    last = [(run.steps[-1].node, run.steps[-1].output) for run in (case.base, case.pr)]
    return last[0] != last[1]


Truth = Literal["regression", "drift"]  # T182: the two truths a case's roots come in


@dataclass(frozen=True)
class SetScore:
    """T182: one method against one truth's roots, each node it names counted on its own."""

    precision: Rate  # of the nodes named, the share that are roots of their case
    recall: Rate  # of the roots, the share named
    exact: Rate  # of the cases with roots, the share where the nodes named are exactly those
    false_flag: Rate | None  # regression roots only: of harmless changed cases, a node named
    inconclusive: int  # cases that name no node and an inconclusive one: misses for recall


def _roots(case: SharedCase, truth: Truth) -> frozenset[str]:
    return case.truth.regression_roots if truth == "regression" else case.truth.drift_roots


def set_score(found: Sequence[tuple[SharedCase, Found]], truth: Truth) -> SetScore:
    """T182. A harmless drift is a correct drift root, so false flags count against regression
    roots only."""
    hits = sum(len(f.root & _roots(c, truth)) for c, f in found)
    rooted = [(c, f) for c, f in found if _roots(c, truth)]
    harmless = [f for c, f in found if c.truth.label == "benign"]
    return SetScore(
        Rate(hits, sum(len(f.root) for _, f in found)),
        Rate(hits, sum(len(_roots(c, truth)) for c, _ in found)),
        Rate(sum(f.root == _roots(c, truth) for c, f in rooted), len(rooted)),
        Rate(sum(bool(f.root) for f in harmless), len(harmless)) if truth == "regression" else None,
        sum(1 for _, f in found if f.inconclusive and not f.root),
    )


@dataclass(frozen=True)
class SharedRow:
    """T182: one class of edit on one build, or all of them, under one method."""

    edits: str
    method: str
    regression: SetScore
    drift: SetScore
    seen: Rate | None = None  # lgcov: of the nodes the edits touch, those whose fingerprint changed


class SharedCaseRow(TypedDict):
    """T182: one case as docs/benchmark-synthetic.json holds it, so a reader can check each."""

    agent: str
    operator: str
    edit: str
    question: str
    label: str
    touched: list[str]
    regression_roots: list[str]
    drift_roots: list[str]
    lgcov_root: list[str]
    lgcov_inconclusive: list[str]
    first_divergence: list[str]
    fingerprints_changed: list[str]


@dataclass(frozen=True)
class SharedReport:
    """T182: the edits the diff can't pin to one node."""

    cases: int
    edits: int
    builds: int
    labels: dict[str, int]
    rows: tuple[SharedRow, ...]
    per_case: tuple[SharedCaseRow, ...]
    end_to_end_caught: Rate  # of regressions, the final answer changed
    end_to_end_false: Rate  # of edits that changed the run and did not regress, the same


CLASS_NAMES = {
    "shared_helper": "Shared helper",
    "shared_model_swap": "Model swap",
    "shared_config": "Shared config",
}
ALL_TOUCHED = "edited node from the diff: all touched nodes"


def shared_report(
    lgcov: Sequence[tuple[SharedCase, Found]],
    changed: Mapping[tuple[str, str, str], frozenset[str]],
) -> SharedReport:
    """T182. `lgcov`: each case with the labels `lgcov ci` ships. `changed`: by edit, the nodes
    whose fingerprint changed. The diff names no single node here, so the edited-node baseline
    names every node the edit touches."""
    cases = [case for case, _ in lgcov]
    named = {
        "lgcov root": [found for _, found in lgcov],
        ALL_TOUCHED: [Found(case.truth.touched) for case in cases],
        "first divergence": [Found(frozenset(first_divergence(case))) for case in cases],
    }
    order = {op: i for i, op in enumerate(dict.fromkeys(case.operator for case in cases))}
    builds = sorted(dict.fromkeys((c.operator, c.agent) for c in cases), key=lambda b: order[b[0]])
    groups = [(f"{CLASS_NAMES.get(op, op)} ({agent})", {(op, agent)}) for op, agent in builds]
    rows = []
    for name, keep in [*groups, ("All", set(builds))]:
        picked = [i for i, c in enumerate(cases) if (c.operator, c.agent) in keep]
        touched = {cases[i].edit: cases[i].truth.touched for i in picked}
        saw = sum(len(changed.get(edit, frozenset()) & nodes) for edit, nodes in touched.items())
        seen = Rate(saw, sum(len(nodes) for nodes in touched.values()))
        for method, found in named.items():
            pairs = [(cases[i], found[i]) for i in picked]
            regression, drift = set_score(pairs, "regression"), set_score(pairs, "drift")
            rows.append(
                SharedRow(name, method, regression, drift, seen if method == "lgcov root" else None)
            )
    per_case = tuple(
        SharedCaseRow(
            agent=c.agent,
            operator=c.operator,
            edit=c.variant,
            question=str(c.base.inputs.get("question", "")),
            label=c.truth.label,
            touched=sorted(c.truth.touched),
            regression_roots=sorted(c.truth.regression_roots),
            drift_roots=sorted(c.truth.drift_roots),
            lgcov_root=sorted(f.root),
            lgcov_inconclusive=sorted(f.inconclusive),
            first_divergence=sorted(first_divergence(c)),
            fingerprints_changed=sorted(changed.get(c.edit, frozenset())),
        )
        for c, f in lgcov
    )
    regressed = [case for case in cases if case.truth.regressed]
    harmless = [case for case in cases if case.truth.label == "benign"]
    return SharedReport(
        len(cases),
        len({case.edit for case in cases}),
        len({case.agent for case in cases}),
        dict(Counter(case.truth.label for case in cases)),
        tuple(rows),
        per_case,
        Rate(sum(map(end_to_end_flags, regressed)), len(regressed)),
        Rate(sum(map(end_to_end_flags, harmless)), len(harmless)),
    )


@dataclass(frozen=True)
class Row:
    method: str
    cases: Rates
    by_edit: Rates


@dataclass(frozen=True)
class Report:
    date: str
    cases: int
    edits: int
    agents: int
    classes: int
    labels: dict[str, int]
    all_edits: tuple[Row, ...]
    single_node: tuple[Row, ...]  # without conditional-edge edits: router and fan-out
    end_to_end_caught: Rate  # of regressions, the final answer changed
    end_to_end_false: Rate  # of edits that changed the run and did not regress, the same
    aa: tuple[tuple[str, AaResult], ...]
    noise: tuple[tuple[str, NoiseRate], ...] = ()  # T211
    per_node: tuple[tuple[str, NodeScore], ...] = ()  # T222: by group of edits, as the rows are
    shared: SharedReport | None = None  # T182: edits the diff can't pin to one node


def _row(name: str, method: Method, cases: Sequence[Case]) -> Row:
    votes: dict[tuple[str, str, str], list[Vote]] = {}
    for case in cases:
        votes.setdefault(case.edit, []).append(votes_for(method, case))
    every = [vote for cast in votes.values() for vote in cast]
    return Row(name, rates(every), rates(by_edit(votes)))


def build_report(
    cases: Sequence[Case],
    lgcov: Method,
    aa: Sequence[tuple[str, AaResult]],
    date: str,
    noise: Sequence[tuple[str, NoiseRate]] = (),
    *,
    labels: Labeller | None = None,
    first_drift: Method | None = None,
    shared: SharedReport | None = None,
) -> Report:
    """`lgcov`: the roots the "lgcov root" row scores. T222: `labels` is the same answer in full,
    for the per-node table, and `first_drift` the rule that row scored before, kept beside it.
    T182: `shared`, the edits the diff can't pin to one node, scored on their own."""
    older = {FIRST_DRIFT: first_drift} if first_drift else {}
    methods = {"lgcov root": lgcov, **older, **METHODS}
    single = [case for case in cases if case.truth.edit_kind == "node_body"]
    groups = (("All", cases), ("Single-node", single)) if labels else ()
    regressed = [case for case in cases if case.truth.regressed]
    harmless = [case for case in cases if case.truth.label == "benign"]
    return Report(
        date,
        len(cases),
        len({case.edit for case in cases}),
        len({case.agent for case in cases}),
        len({case.operator for case in cases}),
        dict(Counter(case.truth.label for case in cases)),
        tuple(_row(name, method, cases) for name, method in methods.items()),
        tuple(_row(name, method, single) for name, method in methods.items()),
        Rate(sum(map(end_to_end_flags, regressed)), len(regressed)),
        Rate(sum(map(end_to_end_flags, harmless)), len(harmless)),
        tuple(aa),
        tuple(noise),
        tuple((name, node_score([(c, labels(c)) for c in group])) for name, group in groups)
        if labels
        else (),
        shared,
    )


def _plain(rate: Rate) -> str:
    return "—" if rate.value is None else f"{rate.value:.2f} ({rate.hits} of {rate.of})"


def _ranged(rate: Rate) -> str:
    if rate.value is None or rate.interval is None:
        return "—"
    low, high = rate.interval
    return f"{rate.value:.2f} ({rate.hits} of {rate.of}; {low:.2f}–{high:.2f})"


def _table(
    groups: Sequence[tuple[str, Sequence[Row]]],
    pick: Callable[[Row], Rates],
    show: Callable[[Rate], str],
) -> list[str]:
    lines = [
        "| Edits | Method | Precision | Recall | Coverage | False flag |",
        "|---|---|---|---|---|---|",
    ]
    for group, rows in groups:
        for row in rows:
            r = pick(row)
            lines.append(
                f"| {group} | {row.method} | {show(r.precision)} | {show(r.recall)} "
                f"| {show(r.coverage)} | {show(r.false_flag)} |"
            )
    return lines


def _method(rows: Sequence[Row], name: str) -> Rates:
    return next(row.cases for row in rows if row.method == name)


def _noise_lines(noise: Sequence[tuple[str, NoiseRate]]) -> list[str]:
    """T211: the noise rule's false alarms and catches. A missed shift is its stated limit."""
    if not noise:
        return []
    k = noise[0][1].k
    rows = []
    for agent, n in noise:
        missed = Rate(n.runs - n.flagged, n.runs)
        reads = "false alarm" if n.arm == "no change" else f"caught; missed {_plain(missed)}"
        rows.append(
            f"| {agent} | {n.arm} | {n.what} | {n.runs} | {_plain(Rate(n.flagged, n.runs))} "
            f"| {reads} |"
        )
    return [
        "### Noise rule",
        "",
        f"The noise rule (`drift_exceeds_noise`), on the seeded-noise agent at K={k}: the "
        f"recording and {k} repeats on base, {k} samples on head. `lgcov bench "
        "--noise-repeats` runs these repeats on an agent, on a real one once so far, with no "
        "change and no false alarm, and the gate still compares one sample. It names drift only "
        "when head's nearest sample "
        "is further from base than base's samples are from each other, so it misses a shift "
        "smaller than the spread. Below the threshold a node reads `no drift detected above noise "
        f"(K={k})`: not cleared, since a small regression can sit inside the spread and the KPI "
        "judge decides that. The noise here is ours (1 answer in 5 is cut short), so these rates "
        "say how the rule behaves, not what a real model would give.",
        "",
        "| Agent | Arm | Head's edit | Runs | Named drift | Reads as |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
    ]


def _node_lines(per_node: Sequence[tuple[str, NodeScore]]) -> list[str]:
    """T222: the shipped labels, node by node."""
    if not per_node:
        return []
    return [
        "### Per node",
        "",
        "`lgcov root` above is the label `lgcov ci` ships. It can name several nodes on one case, "
        "or none, so here each node it names root counts on its own. A case with only an "
        "inconclusive node names no root: a miss for recall, and not counted in precision.",
        "",
        "- **Precision:** of the nodes named root, the share that are the edited node of a "
        "regressed case.",
        "- **Recall:** of regressed cases, the share whose edited node is named root.",
        "- **Exact match:** of regressed cases, the share where the roots named are exactly the "
        "edited node.",
        "- **One side only:** of the nodes named root, the share named because only base or only "
        "the PR ran them.",
        "",
        "| Edits | Precision | Recall | Exact match | Inconclusive cases | One side only |",
        "|---|---|---|---|---|---|",
        *(
            f"| {group} | {_plain(n.precision)} | {_plain(n.recall)} | {_plain(n.exact)} "
            f"| {n.inconclusive} | {_plain(n.one_sided)} |"
            for group, n in per_node
        ),
        "",
    ]


# T182: the two truths, word for word in the doc.
REGRESSION_ROOT = (
    "**Regression root:** a touched node where the edit, applied at that node only and with every "
    "other node as on base, makes the run fail the outcome check."
)
DRIFT_ROOT = (
    "**Drift root:** a touched node whose own output changes when the edit is applied at that "
    "node only, with every other node as on base."
)
CLAIMS_DRIFT = (
    "lgcov's root label claims drift, not harm, since this run has no judges. So a harmless change "
    'lgcov names, like respond\'s on "Order status?", is a correct root under drift roots and a '
    "false root under regression roots."
)


def _maybe(rate: Rate | None) -> str:
    return "—" if rate is None else _plain(rate)


def _shared_lines(shared: SharedReport | None) -> list[str]:
    """T182: the edits the diff can't pin to one node, against both truths."""
    if shared is None:
        return []
    labels = ", ".join(f"{shared.labels.get(k, 0)} {k}" for k in ("regressed", "benign", "inert"))
    harm = [
        f"| {r.edits} | {r.method} | {_plain(r.regression.precision)} "
        f"| {_plain(r.regression.recall)} | {_plain(r.regression.exact)} "
        f"| {_maybe(r.regression.false_flag)} | {r.regression.inconclusive} | {_maybe(r.seen)} |"
        for r in shared.rows
    ]
    drift = [
        f"| {r.edits} | {r.method} | {_plain(r.drift.precision)} | {_plain(r.drift.recall)} "
        f"| {_plain(r.drift.exact)} | {r.drift.inconclusive} |"
        for r in shared.rows
    ]
    every, diff, first = (
        next(r for r in shared.rows if (r.edits, r.method) == ("All", method))
        for method in ("lgcov root", ALL_TOUCHED, "first divergence")
    )
    missed = every.drift.recall.of - every.drift.recall.hits
    harmless = every.drift.precision.hits - every.regression.precision.hits  # drift, no harm
    misses = [
        f"{r.drift.recall.of - r.drift.recall.hits} in {r.edits}, where its fingerprint saw "
        f"{_maybe(r.seen)} of the touched nodes and {r.drift.inconclusive} cases are inconclusive"
        for r in shared.rows
        if r.method == "lgcov root" and r.edits != "All" and r.drift.recall.hits < r.drift.recall.of
    ]
    return [
        "### Edits the diff can't pin to one node",
        "",
        f"T182: {shared.cases} cases from {shared.edits} distinct edits on {shared.builds} builds "
        "of one demo-shaped agent, [support.py](../examples/synthetic_agents/support.py) "
        f"({labels}). In each edit a helper, the model name or a config default is read by 2 or "
        "more nodes, so the diff names no single node. `support-nested` runs respond inside a "
        "subgraph, the shape T220 found in open_deep_research. `support-client` keeps the model "
        "name in a client object built at import. These cases come from our own generator too: "
        "see [Limitations](#limitations).",
        "",
        "Each case is scored against two truths:",
        "",
        f"- {REGRESSION_ROOT}",
        f"- {DRIFT_ROOT}",
        "",
        CLAIMS_DRIFT,
        "",
        "`lgcov root` is the label `lgcov ci` ships, as above. The edited node from the diff is "
        "undefined here, since the diff names no single node, so it is scored as all touched "
        "nodes: every node whose code reaches the edit. First divergence names the first node "
        "whose output differs. Each node a method names counts on its own:",
        "",
        "- **Precision:** of the nodes named, the share that are roots of their case.",
        "- **Recall:** of the roots, the share named.",
        "- **Exact match:** of the cases with roots, the share where the nodes named are exactly "
        "the roots.",
        "- **False flag:** of the cases where the edit changed the run and nothing regressed, the "
        "share where a node is named. Against regression roots only, since a harmless drift is a "
        "correct drift root.",
        "- **Fingerprint saw the edit:** for lgcov, of the nodes the edits touch, the share whose "
        "fingerprint changed, so that replay runs them live.",
        "",
        f"The counts are small, {shared.labels.get('regressed', 0)} regressed cases, so every "
        "ratio comes with them.",
        "",
        "#### Against regression roots",
        "",
        "| Edits | Method | Precision | Recall | Exact match | False flag | Inconclusive cases "
        "| Fingerprint saw the edit |",
        "|---|---|---|---|---|---|---|---|",
        *harm,
        "",
        "#### Against drift roots",
        "",
        "| Edits | Method | Precision | Recall | Exact match | Inconclusive cases |",
        "|---|---|---|---|---|---|",
        *drift,
        "",
        f"- **Against drift roots, lgcov's precision is {_plain(every.drift.precision)} and its "
        f"recall {_plain(every.drift.recall)}.** "
        + (f"It misses {missed}: {'; '.join(misses)}. " if misses else "")
        + "Inside a subgraph it names the node that ran there, by its path, since the base runs "
        "hold the subgraph's units, as `lgcov record --schema 2` writes them (T193).",
        f"- **Against regression roots, lgcov's precision is "
        f"{_plain(every.regression.precision)}.** Of the other "
        f"{every.regression.precision.of - every.regression.precision.hits} nodes it names, "
        f"{harmless} are drift roots that did no harm: correct as drift, false as harm.",
        f"- **All touched nodes names {diff.drift.precision.of} nodes,** with recall "
        f"{_plain(diff.regression.recall)} against regression roots. **First divergence names "
        f"{first.drift.precision.of},** one per changed case, with exact match "
        f"{_plain(first.regression.exact)} against regression roots.",
        f"- **End-to-end names no node.** As a detector, its final answer changed on "
        f"{_plain(shared.end_to_end_caught)} regressed cases and on "
        f"{_plain(shared.end_to_end_false)} harmless ones.",
        "- **Transitive-helper fingerprints aren't the gap:** here and in T220, a helper edit "
        "changed the fingerprint of every node lgcov fingerprints that reaches it.",
        "",
    ]


def render_section(report: Report) -> str:
    """The "## Synthetic" section of docs/benchmark.md."""
    groups = (("All", report.all_edits), ("Single-node", report.single_node))
    lgcov = _method(report.single_node, "lgcov root")
    diff = _method(report.single_node, "edited node from the diff")
    ours = _method(report.all_edits, "lgcov root")
    first = _method(report.all_edits, "first divergence")
    narrow = _method(report.single_node, "first divergence")
    labels = ", ".join(f"{report.labels.get(k, 0)} {k}" for k in ("regressed", "benign", "inert"))
    t182 = (
        "- **Edits the diff can't pin to one node (T182) are scored on their own,** "
        "[below](#edits-the-diff-cant-pin-to-one-node): there the edited node from the diff has "
        "no single node to name."
        if report.shared
        else "- **This benchmark can't show where lgcov adds value.** lgcov's case rests on edits "
        "the diff can't pin to one node: a shared helper, a model or version swap, a shared "
        "config. Those are follow-up T182 and aren't here."
    )
    t182_limits = (
        [
            "- The T182 cases come from one demo-shaped agent with a fake model and the same three "
            "questions, so each edit counts up to three times.",
            "- Not built for T182: a model name a node reads from an environment variable. The "
            "fingerprint doesn't read the environment, so it is predicted to change no "
            "fingerprint, and lgcov to see no drift.",
        ]
        if report.shared
        else []
    )
    lines = [
        "## Synthetic",
        "",
        f"Run {report.date}: {report.cases} cases from {report.edits} distinct edits on "
        f"{report.agents} offline agents, covering all {report.classes} classes in "
        f"[mutations.yaml](mutations.yaml) ({labels}). No paid calls. These numbers never combine "
        "with the real PRs below.",
        "",
        "- **Precision:** of the cases where a method names a node, the share where that node is "
        "the edited one and the edit regressed.",
        "- **Recall:** of regressed cases, the share where the edited node is named.",
        "- **Coverage:** of cases where the edit changed the run, the share where the edited node "
        "is named.",
        "- **False flag:** of cases where the edit changed the run and nothing regressed, the "
        "share where a method names a node.",
        "",
        "Single-node leaves out the router and fan-out edits, which change a conditional edge. "
        "All scores them against the edge's source node, the node that routes: its own output "
        "doesn't change, so a method that names it has to see the router's code.",
        "",
        "### By case",
        "",
        *_table(groups, lambda row: row.cases, _plain),
        "",
        "### One vote per edit",
        "",
        "Cases sharing an edit aren't independent, so each edit also gets one vote, by majority "
        "across its questions. Each cell gives the rate, the count and a 95% Wilson interval.",
        "",
        *_table(groups, lambda row: row.by_edit, _ranged),
        "",
        *_node_lines(report.per_node),
        "### What this says",
        "",
        "- **Edited node from the diff matches or beats lgcov on recall and coverage on "
        f"single-node edits:** recall {_plain(diff.recall)} against {_plain(lgcov.recall)}, and "
        f"coverage {_plain(diff.coverage)} against {_plain(lgcov.coverage)}. Its false-flag rate "
        f"is {_plain(diff.false_flag)}. On precision lgcov is ahead by case, "
        f"{_plain(lgcov.precision)} against {_plain(diff.precision)}, because it stays quiet on "
        "an edit that changed nothing. By edit the two are level, as the table above shows.",
        t182,
        f"- **lgcov flags harmless changes without judges:** its false-flag rate is "
        f"{_plain(lgcov.false_flag)} on single-node edits, against {_plain(diff.false_flag)} for "
        "the edited node from the diff. This run scores drift only, so a harmless change that "
        "moves an output gets a root. A changed run lgcov names no root on is one it left "
        "inconclusive, not one it found harmless, and end-to-end's 0 only means it names no node.",
        "- **On a router or fan-out edit lgcov names the routing node,** from the router's "
        "fingerprint, and first divergence names a node downstream: recall on all edits is "
        f"{_plain(ours.recall)} against {_plain(first.recall)}. On single-node edits it is "
        f"{_plain(lgcov.recall)} against {_plain(narrow.recall)}.",
        f"- **End-to-end names no node.** As a detector, its final answer changed on "
        f"{_plain(report.end_to_end_caught)} regressed cases and on "
        f"{_plain(report.end_to_end_false)} harmless ones.",
        "",
        *_shared_lines(report.shared),
        "### A/A re-runs",
        "",
        "Each question re-run with no edit. These runs aren't in the case count.",
        "",
        "| Agent | Arm | Re-runs | Output flips | Outcome flips | What it is |",
        "|---|---|---|---|---|---|",
        *(
            f"| {agent} | {a.arm} | {a.runs} | {_plain(Rate(a.drifted, a.runs))} "
            f"| {_plain(Rate(a.outcome_flips, a.runs))} | {a.note} |"
            for agent, a in report.aa
        ),
        "",
        *_noise_lines(report.noise),
        "### Limitations",
        "",
        "- Exact match on the edited node: parent-only naming counts as a miss, so an edit to "
        "`sub.inner` is a miss whatever a method says about `sub`.",
        "- The base runs hold the units inside a subgraph, so lgcov labels as `lgcov ci` does on a "
        "baseline recorded with `lgcov record --schema 2` (T193). On a baseline recorded without "
        "it, lgcov calls a subgraph inconclusive, since a subgraph has no fingerprint.",
        "- A router edit counts as a hit whenever the router's code changed and a node drifted "
        "after it. lgcov doesn't check that the run took another branch, and no case here has a "
        "router edit beside a node edit.",
        "- Fake models and small graphs, so these numbers don't predict a real PR.",
        "- The regression label comes from each agent's own outcome check.",
        "- The two order questions gave every edit the same label, so the case-level numbers "
        "count some edits more than their evidence supports.",
        *t182_limits,
        "",
        "Reproduce:",
        "",
        "```bash",
        "uv run lgcov bench --synthetic",
        "```",
        "",
        "This replaces only this section. Plain `lgcov bench` rewrites the whole file, so run this "
        "again after it.",
        "",
    ]
    return "\n".join(lines)


def _rate_json(rate: Rate) -> dict[str, object]:
    return {"hits": rate.hits, "of": rate.of, "value": rate.value}


def _rates_json(found: Rates) -> dict[str, object]:
    fields = ("precision", "recall", "coverage", "false_flag")
    return {field: _rate_json(getattr(found, field)) for field in fields}


def _rows_json(rows: Sequence[Row]) -> list[dict[str, object]]:
    return [
        {"method": r.method, "cases": _rates_json(r.cases), "by_edit": _rates_json(r.by_edit)}
        for r in rows
    ]


def _set_json(score: SetScore) -> dict[str, object]:
    return {
        "precision": _rate_json(score.precision),
        "recall": _rate_json(score.recall),
        "exact": _rate_json(score.exact),
        "false_flag": None if score.false_flag is None else _rate_json(score.false_flag),
        "inconclusive": score.inconclusive,
    }


def _shared_json(shared: SharedReport | None) -> dict[str, object] | None:
    if shared is None:
        return None
    return {
        "cases": shared.cases,
        "edits": shared.edits,
        "builds": shared.builds,
        "labels": shared.labels,
        "rows": [
            {
                "edits": r.edits,
                "method": r.method,
                "regression": _set_json(r.regression),
                "drift": _set_json(r.drift),
                "fingerprint_saw": None if r.seen is None else _rate_json(r.seen),
            }
            for r in shared.rows
        ],
        "end_to_end_caught": _rate_json(shared.end_to_end_caught),
        "end_to_end_false": _rate_json(shared.end_to_end_false),
        "per_case": list(shared.per_case),
    }


def report_json(report: Report) -> str:
    return (
        json.dumps(
            {
                "date": report.date,
                "cases": report.cases,
                "edits": report.edits,
                "agents": report.agents,
                "classes": report.classes,
                "labels": report.labels,
                "all_edits": _rows_json(report.all_edits),
                "single_node": _rows_json(report.single_node),
                "end_to_end_caught": _rate_json(report.end_to_end_caught),
                "end_to_end_false": _rate_json(report.end_to_end_false),
                "aa": [
                    {
                        "agent": agent,
                        "arm": a.arm,
                        "note": a.note,
                        "runs": a.runs,
                        "drifted": a.drifted,
                        "outcome_flips": a.outcome_flips,
                    }
                    for agent, a in report.aa
                ],
                "noise_rule": [{"agent": agent, **asdict(n)} for agent, n in report.noise],
                "per_node": [
                    {
                        "edits": group,
                        "precision": _rate_json(n.precision),
                        "recall": _rate_json(n.recall),
                        "exact": _rate_json(n.exact),
                        "inconclusive": n.inconclusive,
                        "one_sided": _rate_json(n.one_sided),
                    }
                    for group, n in report.per_node
                ],
                "shared": _shared_json(report.shared),
            },
            indent=2,
        )
        + "\n"
    )


def replace_section(doc: str, section: str) -> str:
    """Put the section in `doc`: where one already is, else before "## Real PRs", else at the end.
    Everything else stays as it was."""
    body = section.rstrip("\n") + "\n"
    found = re.search(r"^## Synthetic\n(?:(?!^## ).*\n?)*", doc, re.MULTILINE)
    if found:
        rest = doc[found.end() :]
        return doc[: found.start()] + body + ("\n" + rest if rest else "")
    real = re.search(r"^## Real PRs$", doc, re.MULTILINE)
    if real:
        return doc[: real.start()] + body + "\n" + doc[real.start() :]
    return doc.rstrip("\n") + "\n\n" + body
