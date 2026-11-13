"""T121, D9's spec: a case is a recorded run (base) and its input re-run with one edit (PR).
Its truth: the edited node, whether the edit activated, whether the end-to-end outcome regressed.
Offline only: recorded cassettes and fake models, with no network, API keys or paid calls."""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from random import Random
from typing import Any, Literal

from lgcov.attribution import drift_exceeds_noise
from lgcov.cassette import Cassette, record, replay_run

EditKind = Literal["node_body", "conditional_edge"]
Label = Literal["inert", "benign", "regressed"]
ZERO_NOTE = "0 by construction: a control, not a measurement"
NOISE_NOTE = "wiring test, noise rate set by us, not a real-world flip estimate"


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
    edit_kind: EditKind  # a conditional edge isn't in its node's output: scored on its source node
    activated: bool  # the PR run went differently; this assumes a deterministic agent
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
    noisy: Callable[[int], Any] | None = None  # the unedited agent with a seeded noisy model
    # T211: (size, what the edit does, the noisy agent with that edit in), smallest first.
    shifts: tuple[tuple[str, str, Callable[[int], Any]], ...] = ()


def _trace(run: Cassette) -> list[tuple[str, str]]:
    # Sorted, as replay compares nodes: parallel workers finish in any order.
    return sorted((step.node, json.dumps(step.output, sort_keys=True)) for step in run.steps)


def same_run(a: Cassette, b: Cassette) -> bool:
    return _trace(a) == _trace(b)


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
        activated=not same_run(pr, base),
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


@dataclass(frozen=True)
class AaResult:
    """One arm of re-running an agent with no edit: how often a run differs from its recording."""

    arm: str
    note: str
    runs: int
    drifted: int  # re-runs whose node outputs differ from the recording
    outcome_flips: int  # re-runs that fail the outcome check their recording passes

    @property
    def flip_rate(self) -> float:
        return self.drifted / self.runs if self.runs else 0.0

    @property
    def outcome_flip_rate(self) -> float:
        return self.outcome_flips / self.runs if self.runs else 0.0


def _aa(
    arm: str,
    note: str,
    bases: Sequence[Cassette],
    rerun: Callable[[Cassette, int], Cassette],
    outcome_ok: Callable[[Cassette], bool],
    reruns: int,
) -> AaResult:
    runs = [
        (base, rerun(base, n * len(bases) + i))
        for n in range(reruns)
        for i, base in enumerate(bases)
    ]
    drifted = sum(not same_run(fresh, base) for base, fresh in runs)
    flips = sum(outcome_ok(fresh) != outcome_ok(base) for base, fresh in runs)
    return AaResult(arm, note, len(runs), drifted, flips)


def aa_arms(agent: Agent, reruns: int = 10) -> tuple[AaResult, ...]:
    """Re-run each question with no edit. The first 2 arms are 0 by construction, on a deterministic
    agent and on served nodes. Only the third can flip, at a rate its fake model sets."""
    bases = [record(agent.graph, {"question": question}) for question in agent.questions]

    def re_record(base: Cassette, seed: int) -> Cassette:
        return record(agent.graph, base.inputs)

    def served(base: Cassette, seed: int) -> Cassette:
        return Cassette(base.inputs, list(replay_run(agent.graph, base, (), serve=True).steps))

    arms = [
        _aa("re-record", ZERO_NOTE, bases, re_record, agent.outcome_ok, reruns),
        _aa("served replay", ZERO_NOTE, bases, served, agent.outcome_ok, reruns),
    ]
    noisy = agent.noisy
    if noisy is not None:

        def with_noise(base: Cassette, seed: int) -> Cassette:
            return record(noisy(seed), base.inputs)

        arms.append(_aa("seeded noise", NOISE_NOTE, bases, with_noise, agent.outcome_ok, reruns))
    return tuple(arms)


@dataclass(frozen=True)
class NoiseRate:
    """T211: how often the noise rule named drift in one arm's seeded runs."""

    arm: str
    what: str
    k: int
    runs: int
    flagged: int


NO_CHANGE = "base against base, so every flag is a false alarm"


def noise_rule_rates(agent: Agent, k: int = 3, runs: int = 200) -> tuple[NoiseRate, ...]:
    """T211: `drift_exceeds_noise` on the agent's noisy model, at the run's last node. Each run
    draws k+1 base samples (the recording and k repeats) and k head samples, each with its own
    seed. Head is the same agent (no change), then each of `agent.shifts`, where a flag is a
    catch."""
    noisy = agent.noisy
    if noisy is None:
        return ()

    seen: dict[tuple[int, str], tuple[Any, Any]] = {}  # a seeded graph answers one way: run it once

    def sample(graph: Callable[[int], Any], seed: int, question: str) -> Any:
        built = graph(seed)
        if (id(built), question) not in seen:  # the graph is kept, so its id stays its own
            output = record(built, {"question": question}).steps[-1].output
            seen[id(built), question] = (built, output)
        return seen[id(built), question][1]

    arms = [("no change", NO_CHANGE, noisy)]
    arms += [(f"{size} shift", what, graph) for size, what, graph in agent.shifts]
    rates = []
    for arm, what, head in arms:
        flagged = 0
        for n in range(runs):
            question = agent.questions[n % len(agent.questions)]
            seeds = range(n * (2 * k + 1), (n + 1) * (2 * k + 1))
            base = [sample(noisy, seed, question) for seed in seeds[: k + 1]]
            heads = [sample(head, seed, question) for seed in seeds[k + 1 :]]
            flagged += drift_exceeds_noise(base, heads)
        rates.append(NoiseRate(arm, what, k, runs, flagged))
    return tuple(rates)
