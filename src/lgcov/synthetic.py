"""T121, D9's spec: a case is a recorded run (base) and its input re-run with one edit (PR).
Its truth: the edited node, whether the edit activated, whether the end-to-end outcome regressed.
Offline only: recorded cassettes and fake models, with no network, API keys or paid calls."""

# TODO(T121): A/A re-runs for flip-rate baselines; `activated` assumes a deterministic agent.
# TODO(T121): `lgcov bench --synthetic`: precision, recall and coverage vs edited-node,
# first-divergence and end-to-end baselines.
# TODO(T121): a "synthetic" section in docs/benchmark.md, kept separate from real PRs.

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from random import Random
from typing import Any, Literal

from lgcov.cassette import Cassette, record

EditKind = Literal["node_body", "conditional_edge"]
Label = Literal["inert", "benign", "regressed"]


@dataclass(frozen=True)
class Mutation:
    """One edit an agent offers for an operator class in docs/mutations.yaml."""

    edited_node: str
    variant: str  # what the edit does, for a reader
    graph: Any  # the agent compiled with the edit in. Any: see lgcov.cassette.record
    edit_kind: EditKind = "node_body"


@dataclass(frozen=True)
class GroundTruth:
    edited_node: str
    edit_kind: EditKind  # a conditional edge isn't in its node's output; T175 rules on scoring it
    activated: bool  # the PR run went differently from the recording
    regressed: bool  # the PR run fails the outcome check, which base passes

    @property
    def label(self) -> Label:
        return "regressed" if self.regressed else "benign" if self.activated else "inert"


@dataclass(frozen=True)
class Case:
    operator: str
    seed: int
    variant: str
    base: Cassette
    pr: Cassette
    truth: GroundTruth
    graph: Any  # the edited agent, as in Mutation, so a case can be replayed against it
    agent: str = ""

    @property
    def edit(self) -> tuple[str, str, str]:
        """One edit, whichever question it ran on: cases sharing this aren't independent."""
        return (self.agent, self.operator, self.variant)


@dataclass(frozen=True)
class Agent:
    """A graph the suite edits, the questions it asks, and how it judges an outcome."""

    name: str
    graph: Any  # as in Mutation
    questions: tuple[str, ...]
    mutations: Mapping[str, tuple[Mutation, ...]]
    outcome_ok: Callable[[Cassette], bool]


def _trace(run: Cassette) -> list[tuple[str, Any]]:
    return [(step.node, step.output) for step in run.steps]


def generate(
    operator: str,
    mutations: Sequence[Mutation],
    runs: Sequence[Cassette],
    outcome_ok: Callable[[Cassette], bool],
    seed: int,
) -> Case:
    """One case: the seed picks a recorded run that passes `outcome_ok`, then one mutation."""
    passing = [run for run in runs if outcome_ok(run)]  # else "regressed" would mean nothing
    if not passing:
        raise ValueError("no recorded run passes the outcome check")
    # Random.random is the one draw Python promises to keep across versions, and CI's is newer.
    rng = Random(seed)
    base = passing[int(rng.random() * len(passing))]
    mutation = mutations[int(rng.random() * len(mutations))]
    pr = record(mutation.graph, base.inputs)
    truth = GroundTruth(
        mutation.edited_node,
        mutation.edit_kind,
        activated=_trace(pr) != _trace(base),
        regressed=not outcome_ok(pr),
    )
    return Case(operator, seed, mutation.variant, base, pr, truth, mutation.graph)


def every_case(agent: Agent) -> tuple[Case, ...]:
    """Each edit on each question, once: one run and one mutation leave the seed nothing to pick."""
    bases = [record(agent.graph, {"question": question}) for question in agent.questions]
    return tuple(
        replace(generate(operator, (mutation,), (base,), agent.outcome_ok, 0), agent=agent.name)
        for operator, mutations in agent.mutations.items()
        for mutation in mutations
        for base in bases
    )
