"""T121: synthetic cases on the demo agent: deterministic, offline, and labelled from what the
runs did. Every case the seeds reach is one edit on one recorded question."""

import socket
from collections import Counter
from functools import cache
from pathlib import Path

import pytest
import yaml

from examples.demo_agent.graph import graph as demo_graph
from examples.demo_agent.mutations import MUTATIONS, NOT_ON_DEMO, build, outcome_ok
from lgcov.cassette import Cassette, Step, runs_from_json
from lgcov.snapshot import snapshot_from_compiled
from lgcov.synthetic import Case, Label, Mutation, generate

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
