"""T121: synthetic cases on the demo agent: deterministic, offline, and labelled from what the
runs did. Every case the seeds reach is one edit on one recorded question."""

import socket
from collections import Counter
from dataclasses import replace
from functools import cache
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from examples.demo_agent.graph import graph as demo_graph
from examples.demo_agent.mutations import MUTATIONS, NOT_ON_DEMO, build, outcome_ok
from examples.synthetic_agents import AGENTS, DEMO, FANOUT, fanout, subgraph
from lgcov.cassette import Cassette, Step, record, replay_run, runs_from_json
from lgcov.snapshot import snapshot_from_compiled
from lgcov.synthetic import (
    Agent,
    Case,
    Label,
    Mutation,
    aa_arms,
    every_case,
    generate,
    noise_rule_rates,
    same_run,
)

ROOT = Path(__file__).resolve().parents[1]
RUNS = runs_from_json((ROOT / "examples/demo_agent/cassettes/baseline.json").read_text("utf-8"))
ORDER, HOURS = "Where is my order?", "What are your hours?"
SEEDS = range(40)  # enough to reach every edit on every recorded question

# The class, a word from the edit's description, its label on each recorded question.
TABLE = [
    ("state_key_rename", "order_status", "regressed", "inert"),
    ("retrieval_k_change", "1 to 0", "regressed", "inert"),
    ("tool_schema_change", "near the end", "benign", "inert"),
    ("tool_schema_change", "too far back", "regressed", "inert"),
    ("model_swap", "last 12", "regressed", "benign"),
    ("model_swap", "last 30", "benign", "benign"),
    ("model_swap", "last 60", "benign", "benign"),
    ("prompt_edit", "friendly", "benign", "benign"),
    ("prompt_edit", "same length", "inert", "inert"),
    ("router_flip", "inverted", "regressed", "regressed"),
]
# The class's cases labelled inert, benign and regressed; these go in the PR body.
COUNTS = {
    "state_key_rename": (1, 0, 1),
    "retrieval_k_change": (1, 0, 1),
    "tool_schema_change": (2, 1, 1),
    "model_swap": (0, 5, 1),
    "prompt_edit": (2, 2, 0),
    "router_flip": (0, 0, 2),
}
# Seeds pinned, so a change in the draws, in any process or Python version, shows here.
PICKS = [
    ("state_key_rename", 0, HOURS, "order_status"),
    ("state_key_rename", 1, ORDER, "order_status"),
    ("model_swap", 0, HOURS, "last 60"),
    ("model_swap", 3, ORDER, "last 30"),
    ("model_swap", 4, ORDER, "last 12"),
    ("prompt_edit", 2, HOURS, "same length"),
    ("tool_schema_change", 3, ORDER, "too far back"),
]


def _case(operator: str, seed: int) -> Case:
    return generate(operator, MUTATIONS[operator], RUNS, outcome_ok, seed)


@cache
def _every_case() -> tuple[Case, ...]:
    """Every distinct case the seeds reach."""
    found: dict[tuple[str, str, str], Case] = {}
    for operator in MUTATIONS:
        for seed in SEEDS:
            case = _case(operator, seed)
            found.setdefault((operator, case.variant, case.base.inputs["question"]), case)
    return tuple(found.values())


@pytest.mark.parametrize("seed", [0, 1, 7, 12345])
def test_the_same_seed_gives_the_same_case(seed: int) -> None:
    for operator in MUTATIONS:
        assert _case(operator, seed) == _case(operator, seed)


@pytest.mark.parametrize(("operator", "seed", "question", "edit"), PICKS)
def test_a_seed_picks_the_same_run_and_edit_everywhere(
    operator: str, seed: int, question: str, edit: str
) -> None:
    case = _case(operator, seed)
    assert (case.operator, case.seed) == (operator, seed)
    assert case.base.inputs["question"] == question and edit in case.variant


def test_the_seeds_reach_every_edit_on_every_recorded_question() -> None:
    edits = sum(len(mutations) for mutations in MUTATIONS.values())
    assert len(_every_case()) == edits * len(RUNS)


def test_every_case_has_its_truth_and_the_edited_node_is_in_the_graph() -> None:
    nodes = snapshot_from_compiled(demo_graph).nodes
    for case in _every_case():
        assert case.truth.edited_node in nodes
        assert isinstance(case.truth.activated, bool) and isinstance(case.truth.regressed, bool)
        assert case.variant and case.pr.steps and case.pr.inputs == case.base.inputs


@pytest.mark.parametrize(("operator", "edit", "on_order", "on_hours"), TABLE)
def test_each_edit_gets_its_label_from_what_the_run_did(
    operator: str, edit: str, on_order: str, on_hours: str
) -> None:
    found = {
        case.base.inputs["question"]: case.truth.label
        for case in _every_case()
        if case.operator == operator and edit in case.variant
    }
    assert found == {ORDER: on_order, HOURS: on_hours}


def test_the_label_counts_per_class() -> None:
    counts = Counter((case.operator, case.truth.label) for case in _every_case())
    labels: tuple[Label, ...] = ("inert", "benign", "regressed")
    assert {op: tuple(counts[op, label] for label in labels) for op in MUTATIONS} == COUNTS


def test_each_class_edits_one_node_and_only_router_flip_edits_a_conditional_edge() -> None:
    edits = {(c.operator, c.truth.edited_node, c.truth.edit_kind) for c in _every_case()}
    assert edits == {
        ("state_key_rename", "lookup_order", "node_body"),
        ("retrieval_k_change", "lookup_order", "node_body"),
        ("tool_schema_change", "lookup_order", "node_body"),
        ("model_swap", "respond", "node_body"),
        ("prompt_edit", "respond", "node_body"),
        ("router_flip", "classify", "conditional_edge"),
    }


def test_every_class_in_the_mutation_list_is_covered_or_says_why_not() -> None:
    doc = yaml.safe_load((ROOT / "docs" / "mutations.yaml").read_text("utf-8"))
    assert MUTATIONS.keys() | NOT_ON_DEMO.keys() == {e["operator"] for e in doc["operators"]}
    assert not MUTATIONS.keys() & NOT_ON_DEMO.keys()
    assert all(NOT_ON_DEMO.values())


def test_every_base_passes_the_outcome_check() -> None:
    assert all(outcome_ok(case.base) for case in _every_case())


def test_a_recorded_run_that_fails_the_outcome_check_is_never_picked() -> None:
    failing = Cassette(inputs={"question": ORDER}, steps=[Step("respond", {"answer": "no idea"})])
    assert not outcome_ok(failing)
    mutations = next(iter(MUTATIONS.values()))
    for seed in SEEDS:
        assert generate("x", mutations, [failing, RUNS[1]], outcome_ok, seed).base is RUNS[1]
    with pytest.raises(ValueError, match="no recorded run passes"):
        generate("x", mutations, [failing], outcome_ok, 0)


def test_with_no_edit_the_pr_side_is_the_recording() -> None:
    assert snapshot_from_compiled(build()) == snapshot_from_compiled(demo_graph)
    for seed in SEEDS:
        case = generate("none", (Mutation("respond", "no edit", build()),), RUNS, outcome_ok, seed)
        assert case.pr == case.base and case.truth.label == "inert"


def test_a_prompt_edit_in_graph_py_leaves_the_suite_on_the_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The demo PR's edit. Every case still compares against baseline.json, recorded before it,
    # so neither build() nor DEMO may follow graph.py's SYSTEM_PROMPT.
    monkeypatch.setattr(
        "examples.demo_agent.graph.SYSTEM_PROMPT", "You are a friendly, detailed support agent."
    )
    for run in RUNS:
        assert same_run(record(build(), run.inputs), run)
        assert same_run(record(DEMO.graph, run.inputs), run)


def test_only_the_edited_node_changes_its_code() -> None:
    for case in _every_case():
        recorded = {step.node: step.code for step in case.base.steps}
        changed = {
            step.node
            for step in case.pr.steps
            if step.node in recorded and step.code != recorded[step.node]
        }
        edited = case.truth.edited_node
        in_its_body = case.truth.edit_kind == "node_body" and edited in recorded
        assert changed == ({edited} if in_its_body else set())


def test_generating_every_case_opens_no_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a synthetic case tried to reach the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    for operator in MUTATIONS:
        for seed in SEEDS:
            _case(operator, seed)


# The 2 classes the demo can't hold, on graphs that have a fan-out and a subgraph.
# The agent, the class, a word from the edit, the edited node, its label on each question.
OTHER_GRAPHS = [
    (fanout, "send_fan_out_drop", "first 2", "plan", "regressed", "regressed"),
    (fanout, "send_fan_out_drop", "order question", "plan", "regressed", "inert"),
    (fanout, "prompt_edit", "cheerful", "worker_b", "benign", "benign"),
    (fanout, "retrieval_k_change", "1 to 0", "worker_b", "regressed", "regressed"),
    (subgraph, "subgraph_output_change", "cut", "sub.inner", "regressed", "regressed"),
    (subgraph, "subgraph_output_change", "style", "sub.inner", "benign", "benign"),
    (subgraph, "subgraph_output_change", "built differently", "sub.inner", "inert", "inert"),
    (subgraph, "model_swap", "last 8", "after", "regressed", "regressed"),
]


@pytest.mark.parametrize(
    ("agent", "operator", "edit", "edited_node", "on_order", "on_hours"), OTHER_GRAPHS
)
def test_the_other_graphs_edits_get_their_labels(
    agent: ModuleType, operator: str, edit: str, edited_node: str, on_order: str, on_hours: str
) -> None:
    mutation = next(m for m in agent.MUTATIONS[operator] if edit in m.variant)
    assert mutation.edited_node == edited_node
    found = {}
    for question in (ORDER, HOURS):
        base = record(agent.graph, {"question": question})
        case = generate(operator, (mutation,), (base,), agent.outcome_ok, 0)
        found[question] = case.truth.label
    assert found == {ORDER: on_order, HOURS: on_hours}


def test_every_edit_on_the_other_graphs_is_pinned_above() -> None:
    pinned = Counter((agent, operator) for agent, operator, *_ in OTHER_GRAPHS)
    assert pinned == {
        (agent, operator): len(mutations)
        for agent in (fanout, subgraph)
        for operator, mutations in agent.MUTATIONS.items()
    }


def test_the_fan_out_drop_is_a_conditional_edge_edit_and_the_rest_are_not() -> None:
    assert all(m.edit_kind == "conditional_edge" for m in fanout.MUTATIONS["send_fan_out_drop"])
    others = [m for op, ms in fanout.MUTATIONS.items() if op != "send_fan_out_drop" for m in ms]
    others += [m for ms in subgraph.MUTATIONS.values() for m in ms]
    assert others and all(m.edit_kind == "node_body" for m in others)


def test_every_class_in_the_mutation_list_has_an_edit_on_some_graph() -> None:
    doc = yaml.safe_load((ROOT / "docs" / "mutations.yaml").read_text("utf-8"))
    covered = MUTATIONS.keys() | fanout.MUTATIONS.keys() | subgraph.MUTATIONS.keys()
    assert covered == {e["operator"] for e in doc["operators"]}


@pytest.mark.parametrize("agent", [fanout, subgraph])
def test_with_no_edit_the_other_graphs_record_the_same_run(agent: ModuleType) -> None:
    assert snapshot_from_compiled(agent.build()) == snapshot_from_compiled(agent.graph)
    for question in (ORDER, HOURS):
        base = record(agent.graph, {"question": question})
        assert agent.outcome_ok(base)
        for _ in range(20):  # the fan-out's workers run in parallel: their order mustn't flip
            assert same_run(record(agent.build(), {"question": question}), base)


@cache
def _suite() -> tuple[Case, ...]:
    return tuple(case for agent in AGENTS for case in every_case(agent))


def test_the_suite_has_at_least_50_cases_from_at_least_20_distinct_edits() -> None:
    assert len(_suite()) >= 50
    assert len({case.edit for case in _suite()}) >= 20


def test_each_edit_runs_once_on_each_of_its_agents_questions() -> None:
    assert len(_suite()) == len(set(_identity(case) for case in _suite()))
    for agent in AGENTS:
        edits = sum(len(mutations) for mutations in agent.mutations.values())
        assert sum(case.agent == agent.name for case in _suite()) == edits * len(agent.questions)


def _identity(case: Case) -> tuple[tuple[str, str, str], str]:
    return (case.edit, case.base.inputs["question"])


def test_the_suite_reaches_all_8_classes_and_every_label() -> None:
    doc = yaml.safe_load((ROOT / "docs" / "mutations.yaml").read_text("utf-8"))
    assert {case.operator for case in _suite()} == {e["operator"] for e in doc["operators"]}
    assert {case.truth.label for case in _suite()} == {"inert", "benign", "regressed"}


def test_the_suite_is_not_one_answer_per_edit() -> None:
    labels: dict[tuple[str, str, str], set[str]] = {}
    for case in _suite():
        labels.setdefault(case.edit, set()).add(case.truth.label)
    # An edit whose label doesn't depend on the question only repeats itself across questions.
    assert sum(len(found) > 1 for found in labels.values()) >= 8


def test_every_edit_in_the_suite_is_replayable_from_its_case() -> None:
    for case in _suite():
        assert same_run(record(case.graph, case.base.inputs), case.pr)


def test_a_question_whose_base_fails_its_outcome_check_stops_the_suite() -> None:
    broken = replace(DEMO, questions=("Where is my order number 42 right now?",))
    with pytest.raises(ValueError, match="no recorded run passes"):
        every_case(broken)


def test_the_original_20_cases_are_still_in_the_suite() -> None:
    suite = {(c.operator, c.variant, c.base.inputs["question"]) for c in _suite()}
    assert {(c.operator, c.variant, c.base.inputs["question"]) for c in _every_case()} <= suite


def test_the_suites_counts() -> None:
    assert len(_suite()) == 72
    assert len({case.edit for case in _suite()}) == 24
    assert Counter(case.truth.label for case in _suite()) == {
        "regressed": 31,
        "benign": 24,
        "inert": 17,
    }


@pytest.mark.parametrize("agent", AGENTS)
def test_a_rerun_with_no_edit_never_flips_in_the_deterministic_arms(agent: Agent) -> None:
    arms = {result.arm: result for result in aa_arms(agent, reruns=3)}
    for arm in ("re-record", "served replay"):
        assert arms[arm].runs == 3 * len(agent.questions)
        assert (arms[arm].drifted, arms[arm].outcome_flips) == (0, 0)
        assert (arms[arm].flip_rate, arms[arm].outcome_flip_rate) == (0.0, 0.0)


@pytest.mark.parametrize("agent", AGENTS)
def test_served_replay_reports_no_drift_on_an_unedited_agent(agent: Agent) -> None:
    for question in agent.questions:
        base = record(agent.graph, {"question": question})
        assert replay_run(agent.graph, base, (), serve=True).drifts == ()


def test_only_the_demo_has_a_noise_arm() -> None:
    assert [a.name for a in AGENTS if a.noisy is not None] == ["demo"]
    assert "seeded noise" not in {result.arm for result in aa_arms(FANOUT, reruns=1)}


def test_the_noise_arm_flips_at_the_counts_we_pin_and_says_what_it_is() -> None:
    noise = next(result for result in aa_arms(DEMO) if result.arm == "seeded noise")
    assert noise.note == "wiring test, noise rate set by us, not a real-world flip estimate"
    assert (noise.runs, noise.drifted, noise.outcome_flips) == (30, 5, 4)


def test_noise_can_change_a_run_without_failing_its_outcome() -> None:
    noise = next(result for result in aa_arms(DEMO) if result.arm == "seeded noise")
    assert 0 < noise.outcome_flips < noise.drifted


def test_the_noise_arm_gives_the_same_numbers_every_time() -> None:
    assert aa_arms(DEMO) == aa_arms(DEMO)


def test_the_suite_and_the_a_a_arms_open_no_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a synthetic run tried to reach the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    for agent in AGENTS:
        every_case(agent)
        aa_arms(agent, reruns=2)


def _reordered(run: Cassette) -> Cassette:
    head, *workers, tail = run.steps
    return Cassette(run.inputs, [head, *reversed(workers), tail])


def test_parallel_workers_finishing_in_another_order_is_not_a_different_run() -> None:
    base = record(FANOUT.graph, {"question": ORDER})
    assert _reordered(base) != base and same_run(_reordered(base), base)
    head, _, *rest = base.steps
    assert not same_run(Cassette(base.inputs, [head, Step("worker_a", {"parts": []}), *rest]), base)


def test_a_fan_out_that_only_finished_in_another_order_is_labelled_inert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = record(FANOUT.graph, {"question": ORDER})
    monkeypatch.setattr("lgcov.synthetic.record", lambda graph, inputs, like: _reordered(base))
    mutation = Mutation("plan", "no edit", FANOUT.graph)
    assert generate("x", (mutation,), (base,), FANOUT.outcome_ok, 0).truth.label == "inert"


def test_t121_has_no_todos_left() -> None:
    assert "TODO(T121)" not in (ROOT / "src" / "lgcov" / "synthetic.py").read_text("utf-8")


# T211: how often the noise rule names drift, on the seeded-noise agent. Offline.
def test_the_noise_rule_is_measured_with_no_change_and_at_two_shift_sizes() -> None:
    rates = {r.arm: r for r in noise_rule_rates(DEMO, k=3, runs=200)}
    assert list(rates) == ["no change", "small shift", "large shift"]
    assert all((r.k, r.runs) == (3, 200) for r in rates.values())
    assert rates["small shift"].what == "the prompt becomes friendly and detailed"
    assert rates["large shift"].what == "the prompt ends on a word the outcome check looks for"
    assert noise_rule_rates(DEMO, k=3, runs=200) == tuple(rates.values())  # seeded
    assert noise_rule_rates(FANOUT) == ()  # no noisy model
    flagged = {arm: r.flagged for arm, r in rates.items()}
    assert flagged == {"no change": 1, "small shift": 68, "large shift": 112}  # of 200 each
