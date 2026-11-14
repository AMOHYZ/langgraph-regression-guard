"""lgcov-round.yaml: what a round locks and how it is compared with the gate's settings (pure)."""

import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from lgcov.gate_round import (
    Draft,
    GateNow,
    JudgeLock,
    Reference,
    Round,
    case_ids,
    case_set_hash,
    lock_round,
    parse_draft,
    parse_round,
    prompt_hash,
    round_digest,
    round_problems,
    round_to_yaml,
)
from lgcov.taxonomy import KPISpec

A, B = "a" * 40, "b" * 40
JUDGE = JudgeLock("jev-1", "0.1.0", "c" * 64)
NOW = GateNow(
    case_ids=("id-1", "id-2"),
    judge=JUDGE,
    margins={"grounded": 0.02, "polite": 0.05},
    alpha=0.05,
    power=0.8,
    k_gate=2,
)
DRAFT_DOC: dict[str, object] = {
    "version": 1,
    "gate_cases": {"path": "cassettes/baseline.json"},
    "kpis": [{"name": "grounded", "tags": ["safety", "core"]}],
    "delta_1": -0.01,
    "prior_rho": 0.6,
    "candidates": [A],
    "stop_rules": {"max_cases": 800, "on_block": "stop", "strict": True, "max_usd": 2.5},
}
SPEC = KPISpec(
    name="grounded",
    failure_mode="drift",
    node="respond",
    judge="jev",
    description="Stays grounded.",
    rubric="Is the answer grounded?",
)


def _draft() -> Draft:
    return parse_draft(DRAFT_DOC)


def _locked() -> Round:
    return lock_round(_draft(), NOW, 0)


def _doc() -> dict[str, object]:
    doc: dict[str, object] = yaml.safe_load(round_to_yaml(_locked()))
    return doc


# --- The case set.


def _baseline(*inputs: object) -> dict[str, object]:
    return {"runs": [{"inputs": i, "steps": []} for i in inputs]}


def test_a_cases_id_comes_from_its_inputs_alone() -> None:
    one = case_ids(_baseline({"q": "hello", "n": 1}))
    assert case_ids(_baseline({"n": 1, "q": "hello"})) == one  # the order of keys is no change
    assert case_ids({"runs": [{"inputs": {"n": 1, "q": "hello"}, "steps": ["a step"]}]}) == one
    assert case_ids(_baseline({"q": "hello", "n": 2})) != one
    assert len(one[0]) == 16


def test_the_case_set_hash_ignores_order_and_counts_every_case() -> None:
    assert case_set_hash(("a", "b")) == case_set_hash(("b", "a"))
    assert case_set_hash(("a", "b")) != case_set_hash(("a",))
    assert case_set_hash(("a", "a", "b")) != case_set_hash(("a", "b"))
    assert case_set_hash(("ab",)) != case_set_hash(("a", "b"))


@pytest.mark.parametrize(
    "doc",
    [{}, [], {"runs": {}}, {"runs": []}, {"runs": ["x"]}, {"runs": [{"steps": []}]}],
    ids=["no runs key", "a list", "runs a mapping", "no runs", "a run a string", "no inputs"],
)
def test_a_baseline_with_no_cases_is_refused(doc: object) -> None:
    with pytest.raises(ValueError):
        case_ids(doc)


# --- The prompt.


@pytest.mark.parametrize(
    "changes",
    [
        {"rubric": "Is it grounded in the sources?"},
        {"label_hints": {"pass": "cites a source"}},
        {"labels": ("yes", "no"), "score_map": {"yes": 1.0, "no": 0.0}},
        {"score_map": {"pass": 1.0, "fail": 0.5}},
        {"check": {"type": "node_visited", "node": "respond"}},
        {"judge": "code"},
        {"node": "final"},
        {"name": "grounded_v2"},
    ],
    ids=lambda c: next(iter(c)),
)
def test_what_decides_a_verdict_is_in_the_prompt_hash(changes: Mapping[str, object]) -> None:
    changed = replace(SPEC, **changes)  # type: ignore[arg-type]  # each key is a KPISpec field
    assert prompt_hash([changed]) != prompt_hash([SPEC])


def test_what_doesnt_decide_a_verdict_isnt_in_the_prompt_hash() -> None:
    same = replace(SPEC, description="Reworded.", margin=0.01, failure_mode="other")
    assert prompt_hash([same]) == prompt_hash([SPEC])


def test_the_prompt_hash_covers_every_kpi_in_any_order() -> None:
    other = replace(SPEC, name="polite", rubric="Is it polite?")
    assert prompt_hash([SPEC, other]) == prompt_hash([other, SPEC])
    assert prompt_hash([SPEC, other]) != prompt_hash([SPEC])


# --- Locking a round.


def test_a_locked_round_says_what_the_gate_uses_and_what_the_person_wrote() -> None:
    doc = _doc()
    assert doc["version"] == 1
    assert doc["round"] == 1
    assert doc["gate_cases"] == {
        "path": "cassettes/baseline.json",
        "sha256": case_set_hash(NOW.case_ids),
    }
    assert doc["judge"] == {"model": "jev-1", "version": "0.1.0", "prompt_sha256": "c" * 64}
    assert doc["kpis"] == [
        {"name": "grounded", "tags": ["safety", "core"], "margin": 0.02},
        {"name": "polite", "tags": [], "margin": 0.05},
    ]
    assert (doc["alpha"], doc["power"], doc["k_gate"]) == (0.05, 0.8, 2)
    assert (doc["delta_1"], doc["prior_rho"]) == (-0.01, 0.6)
    assert doc["candidates"] == [A]
    assert doc["stop_rules"] == DRAFT_DOC["stop_rules"]
    assert doc["exposure_floor"] == 0
    assert list(doc)[-1] == "digest"


def test_a_locked_round_reads_back_the_same_and_holds() -> None:
    locked = _locked()
    assert parse_round(yaml.safe_load(round_to_yaml(locked))) == locked
    assert round_problems(locked, NOW, A, 0) == []


def test_the_next_round_takes_what_the_person_wrote_and_recomputes_the_rest() -> None:
    doc = _doc()
    moved = {**NOW.margins, "grounded": 0.01}
    next_round = lock_round(parse_draft(doc), replace(NOW, margins=moved), 7)
    assert next_round.round == 2
    assert next_round.exposure_floor == 7
    assert next_round.kpis[0].margin == 0.01
    assert next_round.kpis[0].tags == ("safety", "core")
    assert next_round.candidates == (A,)
    assert next_round.digest != _locked().digest


def test_a_new_round_cant_start_over_a_ledger_that_lost_history() -> None:
    draft = parse_draft({**_doc(), "exposure_floor": 8})
    with pytest.raises(ValueError, match="exposure history lost: .* 7 entries .* started with 8"):
        lock_round(draft, NOW, 7)
    assert lock_round(draft, NOW, 8).exposure_floor == 8


def test_a_round_cant_tag_a_kpi_the_taxonomy_doesnt_have() -> None:
    draft = parse_draft({**DRAFT_DOC, "kpis": [{"name": "no_such_kpi"}]})
    with pytest.raises(ValueError, match="no_such_kpi"):
        lock_round(draft, NOW, 0)


def test_a_round_cant_lock_a_judge_with_no_pinned_model() -> None:
    with pytest.raises(ValueError, match="pin jev_model.*grounded"):
        lock_round(_draft(), replace(NOW, unpinned=("grounded",)), 0)


# --- Comparing it with now.


def _now(**changes: object) -> GateNow:
    return replace(NOW, **changes)  # type: ignore[arg-type]  # each key is a GateNow field


@pytest.mark.parametrize(
    ("now", "problem"),
    [
        (_now(case_ids=("id-1",)), "gate case set: locked "),
        (_now(judge=replace(JUDGE, model="jev-2")), "judge model: locked jev-1, now jev-2"),
        (_now(judge=replace(JUDGE, model=None)), "judge model: locked jev-1, now none"),
        (_now(judge=replace(JUDGE, version="9")), "judge version: locked 0.1.0, now 9"),
        (_now(judge=replace(JUDGE, prompt_sha256="d" * 64)), "judge prompt hash: locked ccc"),
        (_now(alpha=0.1), "alpha: locked 0.05, now 0.1"),
        (_now(power=0.9), "power: locked 0.8, now 0.9"),
        (_now(k_gate=3), "K_gate: locked 2, now 3"),
        (
            _now(margins={"grounded": 0.03, "polite": 0.05}),
            "KPI grounded margin: locked 0.02, now 0.03",
        ),
        (_now(margins={"polite": 0.05}), "KPI grounded: in the round, not in the taxonomy"),
        (
            _now(margins={**NOW.margins, "new": 0.05}),
            "KPI new: in the taxonomy, not in the round",
        ),
    ],
)
def test_each_locked_value_that_moved_is_named(now: GateNow, problem: str) -> None:
    problems = round_problems(_locked(), now, A, 0)
    assert len(problems) == 1
    assert problem in problems[0]


def test_a_candidate_not_in_the_list_is_refused_by_its_full_comparison() -> None:
    locked = _locked()
    assert round_problems(locked, NOW, A, 0) == []
    assert round_problems(locked, NOW, B, 0) == [
        "candidate `bbbbbbb` is not locked in round 1 (locked: `aaaaaaa`)"
    ]
    assert round_problems(locked, NOW, A[:7], 0) == [
        "candidate `aaaaaaa` is not locked in round 1 (locked: `aaaaaaa`)"
    ]


def test_no_candidate_is_a_problem_and_says_what_to_do() -> None:
    problems = round_problems(_locked(), NOW, None, 0)
    assert len(problems) == 1
    assert "pass --candidate" in problems[0]


def test_a_ledger_below_the_floor_is_a_problem() -> None:
    locked = lock_round(_draft(), NOW, 5)
    assert round_problems(locked, NOW, A, 5) == []
    assert round_problems(locked, NOW, A, 9) == []
    assert round_problems(locked, NOW, A, 4) == [
        "exposure history lost: the ledger has 4 entries and this round started with 5; "
        "restore it from git"
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"round": 2},
        {"alpha": 0.5},
        {"power": 0.5},
        {"delta_1": 0.5},
        {"prior_rho": 0.1},
        {"k_gate": 9},
        {"candidates": [A, B]},
        {"stop_rules": {"max_cases": 1}},
        {"exposure_floor": 1},
        {"gate_cases": {"path": "other.json", "sha256": "e" * 64}},
        {"judge": {"model": "x", "version": "y", "prompt_sha256": "f" * 64}},
        {"kpis": [{"name": "grounded", "tags": ["moved"], "margin": 0.02}]},
    ],
    ids=lambda c: next(iter(c)),
)
def test_a_hand_edit_to_any_field_breaks_the_digest(changes: dict[str, object]) -> None:
    edited = parse_round({**_doc(), **changes})
    assert "its digest doesn't match" in round_problems(edited, NOW, A, 0)[0]


# --- The reference round (T158): what a later round's cumulative loss will be counted from.

ROUND_1_DIGEST = "60485ea9991390bab309586bc83c4044f97864448b08c32227889a452a16deb2"


def _next(doc: dict[str, object]) -> dict[str, object]:
    """The file after one more `lgcov round new`."""
    again: dict[str, object] = yaml.safe_load(round_to_yaml(lock_round(parse_draft(doc), NOW, 0)))
    return again


def test_the_first_round_is_the_reference_and_has_no_pointer() -> None:
    """Its file and digest are what they were before T158, byte for byte."""
    assert _locked().reference is None
    assert "reference" not in _doc()
    assert _locked().digest == ROUND_1_DIGEST


def test_the_second_round_points_at_the_first_by_number_and_digest() -> None:
    second = _next(_doc())
    assert second["reference"] == {"round": 1, "digest": ROUND_1_DIGEST}
    assert list(second)[-2:] == ["reference", "digest"]
    locked = parse_round(second)
    assert locked.reference == Reference(1, ROUND_1_DIGEST)
    assert round_problems(locked, NOW, A, 0) == []


def test_later_rounds_carry_the_reference_unchanged() -> None:
    third = _next(_next(_doc()))
    assert third["round"] == 3
    assert third["reference"] == {"round": 1, "digest": ROUND_1_DIGEST}


def test_the_reference_is_in_the_digest() -> None:
    second = _next(_doc())
    for moved in ({"round": 1, "digest": "e" * 64}, None):
        fields = {k: v for k, v in second.items() if k != "reference"}
        edited = parse_round(fields if moved is None else {**fields, "reference": moved})
        assert "its digest doesn't match" in round_problems(edited, NOW, A, 0)[0]


def test_a_reference_hashes_only_the_keys_it_has() -> None:
    """A key added to `reference` later (a `path` guard re-hashes) can then be left out of the
    hash when absent, so a round locked before it keeps its digest. This pins today's bytes."""
    second = parse_round(_next(_doc()))
    assert second.digest == "2f97efa7ee4b5ec93aff040a3aa7318feaaddb01926967a1d3df25a40bb9a9e2"


def test_a_round_after_the_first_needs_a_reference() -> None:
    bare = replace(parse_round(_next(_doc())), reference=None)
    bare = replace(bare, digest=round_digest(bare))  # as someone who recomputed it would
    assert round_problems(bare, NOW, A, 0) == [
        "round 2 has no reference: only a first round can be one; run `lgcov round new` again"
    ]


@pytest.mark.parametrize("pointed", [2, 3])
def test_a_reference_must_be_an_earlier_round(pointed: int) -> None:
    second = parse_round(_next(_doc()))
    moved = replace(second, reference=Reference(pointed, ROUND_1_DIGEST))
    moved = replace(moved, digest=round_digest(moved))
    assert round_problems(moved, NOW, A, 0) == [f"reference: round {pointed} is not before round 2"]


def test_a_person_cant_write_the_reference_in_a_draft() -> None:
    draft = parse_draft({**DRAFT_DOC, "reference": {"round": 1, "digest": ROUND_1_DIGEST}})
    with pytest.raises(ValueError, match="'reference' is written by `lgcov round new`"):
        lock_round(draft, NOW, 0)


@pytest.mark.parametrize(
    "reference",
    [
        "e" * 64,
        {"round": 1},
        {"digest": "e" * 64},
        {"round": 0, "digest": "e" * 64},
        {"round": 1, "digest": "short"},
        {"round": 1, "digest": "e" * 64, "path": "rounds/1.yaml"},
    ],
    ids=["bare digest", "no digest", "no round", "round 0", "short digest", "unknown key"],
)
def test_a_bad_reference_is_refused(reference: object) -> None:
    with pytest.raises(ValueError, match="reference"):
        parse_round({**_next(_doc()), "reference": reference})


# --- Parsing.

SHA_256 = "d" * 64


def test_both_sha_formats_are_candidates() -> None:
    assert parse_draft({**DRAFT_DOC, "candidates": [A, SHA_256]}).candidates == (A, SHA_256)


def test_a_draft_needs_only_what_a_person_writes() -> None:
    assert parse_draft({**DRAFT_DOC, "kpis": []}).tags == {}
    no_kpis = {k: v for k, v in DRAFT_DOC.items() if k != "kpis"}
    assert parse_draft(no_kpis).tags == {}


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 2},
        {"unknown": 1},
        {"candidates": []},
        {"candidates": "abc"},
        {"candidates": ["abc1234"]},
        {"candidates": [A.upper()]},
        {"candidates": [1234567]},
        {"candidates": [A, A]},
        {"delta_1": "0.1"},
        {"delta_1": True},
        {"delta_1": 1.5},
        {"prior_rho": -1.5},
        {"prior_rho": None},
        {"stop_rules": []},
        {"stop_rules": {"max_cases": [1]}},
        {"stop_rules": {"max_cases": None}},
        {"gate_cases": "cassettes/baseline.json"},
        {"gate_cases": {}},
        {"gate_cases": {"path": ""}},
        {"gate_cases": {"path": "a", "other": "b"}},
        {"kpis": {"grounded": []}},
        {"kpis": ["grounded"]},
        {"kpis": [{"tags": []}]},
        {"kpis": [{"name": "grounded", "tags": "safety"}]},
        {"kpis": [{"name": "grounded", "tags": [1]}]},
        {"kpis": [{"name": "grounded", "other": 1}]},
        {"kpis": [{"name": "grounded"}, {"name": "grounded"}]},
        {"round": 0},
        {"round": True},
        {"exposure_floor": -1},
    ],
    ids=str,
)
def test_a_bad_draft_is_refused(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        parse_draft({**DRAFT_DOC, **changes})


@pytest.mark.parametrize("key", ["gate_cases", "delta_1", "prior_rho", "candidates", "stop_rules"])
def test_a_draft_missing_a_field_a_person_writes_is_refused(key: str) -> None:
    with pytest.raises(ValueError, match=key):
        parse_draft({k: v for k, v in DRAFT_DOC.items() if k != key})


@pytest.mark.parametrize("raw", [None, [], "text"], ids=str)
def test_a_round_file_thats_not_a_mapping_is_refused(raw: object) -> None:
    with pytest.raises(ValueError):
        parse_draft(raw)
    with pytest.raises(ValueError):
        parse_round(raw)


@pytest.mark.parametrize(
    "key", ["round", "judge", "kpis", "alpha", "power", "k_gate", "exposure_floor", "digest"]
)
def test_a_locked_round_missing_a_computed_field_is_refused(key: str) -> None:
    with pytest.raises(ValueError, match=key):
        parse_round({k: v for k, v in _doc().items() if k != key})


@pytest.mark.parametrize(
    "changes",
    [
        {"gate_cases": {"path": "a"}},
        {"gate_cases": {"path": "a", "sha256": "short"}},
        {"alpha": 0},
        {"alpha": 1},
        {"power": 1.5},
        {"k_gate": -1},
        {"k_gate": True},
        {"judge": "jev-1"},
        {"judge": {"model": "m", "version": "v"}},
        {"judge": {"model": "m", "version": "v", "prompt_sha256": "short"}},
        {"judge": {"model": 1, "version": "v", "prompt_sha256": "c" * 64}},
        {"judge": {"model": "m", "version": "", "prompt_sha256": "c" * 64}},
        {"judge": {"model": "m", "version": "v", "prompt_sha256": "c" * 64, "other": 1}},
        {"kpis": [{"name": "grounded", "tags": []}]},
        {"kpis": [{"name": "grounded", "margin": 2}]},
        {"digest": 1},
    ],
    ids=str,
)
def test_a_bad_locked_round_is_refused(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        parse_round({**_doc(), **changes})


def test_the_draft_in_the_docs_is_a_valid_draft() -> None:
    docs = Path(__file__).resolve().parents[1] / "docs" / "round.md"
    block = re.search(r"```yaml\n(.*?)```", docs.read_text(encoding="utf-8"), re.DOTALL)
    assert block is not None
    draft = parse_draft(yaml.safe_load(block[1]))
    assert draft.tags == {"respond_runs": ("flow",)}
    assert draft.stop_rules == {"max_cases": 800}


def test_a_judge_with_no_model_is_valid_in_a_round() -> None:
    locked = lock_round(_draft(), replace(NOW, judge=replace(JUDGE, model=None)), 0)
    assert parse_round(yaml.safe_load(round_to_yaml(locked))).judge.model is None


def test_a_judges_id_names_its_model_version_and_prompt() -> None:
    assert JUDGE.id == "jev-1@0.1.0/cccccccccccc"
    assert replace(JUDGE, model=None).id == "no model@0.1.0/cccccccccccc"
