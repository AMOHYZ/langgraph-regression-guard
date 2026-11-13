"""T121: scoring the synthetic suite. A method names nodes on a case; a vote records what that
got right. The rates are the same sums over cases and over edits. Pure: no runs happen here."""

import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass

from lgcov.stats import wilson
from lgcov.synthetic import AaResult, Case, NoiseRate

Method = Callable[[Case], set[str]]


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


def first_divergence(case: Case) -> set[str]:
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


METHODS: dict[str, Method] = {
    "edited node from the diff": _edited_node,
    "first divergence": first_divergence,
    "end-to-end": _no_node,  # flags a run, names no node: end_to_end_flags
}


def end_to_end_flags(case: Case) -> bool:
    """The final answer changed: what a test on only the agent's output would see."""
    last = [(run.steps[-1].node, run.steps[-1].output) for run in (case.base, case.pr)]
    return last[0] != last[1]


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
) -> Report:
    methods = {"lgcov root": lgcov, **METHODS}
    single = [case for case in cases if case.truth.edit_kind == "node_body"]
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
        f"recording and {k} repeats on base, {k} samples on head. Nothing runs repeats on a real "
        "agent yet: `lgcov bench` and the gate still compare one sample. It names drift only when "
        "head's nearest sample "
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


def render_section(report: Report) -> str:
    """The "## Synthetic" section of docs/benchmark.md."""
    groups = (("All", report.all_edits), ("Single-node", report.single_node))
    lgcov = _method(report.single_node, "lgcov root")
    diff = _method(report.single_node, "edited node from the diff")
    ours = _method(report.all_edits, "lgcov root")
    first = _method(report.all_edits, "first divergence")
    labels = ", ".join(f"{report.labels.get(k, 0)} {k}" for k in ("regressed", "benign", "inert"))
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
        "### What this says",
        "",
        "- **Edited node from the diff matches or beats lgcov on recall and coverage on "
        f"single-node edits:** recall {_plain(diff.recall)} against {_plain(lgcov.recall)}, and "
        f"coverage {_plain(diff.coverage)} against {_plain(lgcov.coverage)}. Its false-flag rate "
        f"is {_plain(diff.false_flag)}. On precision lgcov is ahead by case, "
        f"{_plain(lgcov.precision)} against {_plain(diff.precision)}, because it stays quiet on "
        "an edit that changed nothing. By edit the two are level, as the table above shows.",
        "- **This benchmark can't show where lgcov adds value.** lgcov's case rests on edits the "
        "diff can't pin to one node: a shared helper, a model or version swap, a shared config. "
        "Those are follow-up T182 and aren't here.",
        f"- **lgcov flags harmless changes without judges:** its false-flag rate is "
        f"{_plain(lgcov.false_flag)} on single-node edits. This run scores drift only. The rate "
        "can't separate the methods: drift and the diff both name a node on every harmless change, "
        "and end-to-end's 0 only means it names no node.",
        "- **First divergence ties lgcov on single-node edits,** because the first node to drift "
        "is lgcov's root rule there. On a router or fan-out edit lgcov names the routing node, "
        "from the router's fingerprint, and first divergence names a node downstream: recall on "
        f"all edits is {_plain(ours.recall)} against {_plain(first.recall)}.",
        f"- **End-to-end names no node.** As a detector, its final answer changed on "
        f"{_plain(report.end_to_end_caught)} regressed cases and on "
        f"{_plain(report.end_to_end_false)} harmless ones.",
        "",
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
        "- Exact match on the edited node: parent-only naming counts as a miss, so lgcov's `sub` "
        "doesn't score for an edit to `sub.inner`. That is every single-node miss lgcov makes.",
        "- A router edit counts as a hit whenever the router's code changed and a node drifted "
        "after it. lgcov doesn't check that the run took another branch, and no case here has a "
        "router edit beside a node edit.",
        "- Fake models and small graphs, so these numbers don't predict a real PR.",
        "- The regression label comes from each agent's own outcome check.",
        "- The two order questions gave every edit the same label, so the case-level numbers "
        "count some edits more than their evidence supports.",
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
