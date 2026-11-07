"""T121: scoring the synthetic suite. A method names nodes on a case; a vote records what that
got right. The rates are the same sums over cases and over edits. Pure: no runs happen here."""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from lgcov.stats import wilson
from lgcov.synthetic import Case

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
