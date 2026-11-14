"""exposure.json: which judge versions and candidates saw each case. It only ever grows (pure)."""

import json

import pytest

from lgcov.exposure import CaseExposure, entries, exposure_to_json, parse_exposure, record

A, B = "a" * 40, "b" * 40
JUDGE, NEW_JUDGE = "jev-1@0.1.0/abc", "jev-2@0.1.0/abc"


def test_record_adds_the_candidate_judge_and_round_to_every_case() -> None:
    ledger = record({}, ("c1", "c2"), A, JUDGE, 1)
    assert ledger == {
        "c1": CaseExposure((A,), (JUDGE,), (1,)),
        "c2": CaseExposure((A,), (JUDGE,), (1,)),
    }


def test_record_keeps_a_cases_history_and_a_case_outside_this_set() -> None:
    first = record({}, ("c1", "c2"), A, JUDGE, 1)
    ledger = record(first, ("c2", "c3"), B, NEW_JUDGE, 2)
    assert ledger["c1"] == CaseExposure((A,), (JUDGE,), (1,))
    assert ledger["c2"] == CaseExposure((A, B), (JUDGE, NEW_JUDGE), (1, 2))
    assert ledger["c3"] == CaseExposure((B,), (NEW_JUDGE,), (2,))


def test_record_leaves_what_it_was_given_alone() -> None:
    first = record({}, ("c1",), A, JUDGE, 1)
    record(first, ("c1",), B, NEW_JUDGE, 2)
    assert first == {"c1": CaseExposure((A,), (JUDGE,), (1,))}


def test_recording_the_same_thing_twice_adds_nothing() -> None:
    once = record({}, ("c1",), A, JUDGE, 1)
    assert record(once, ("c1",), A, JUDGE, 1) == once
    assert entries(record(once, ("c1",), A, JUDGE, 1)) == entries(once)


def test_entries_counts_everything_in_the_ledger_and_recording_never_lowers_it() -> None:
    assert entries({}) == 0
    first = record({}, ("c1", "c2"), A, JUDGE, 1)
    assert entries(first) == 6
    assert entries(record(first, ("c3",), B, JUDGE, 2)) == 9


def test_the_json_round_trips_with_sorted_keys_and_lists() -> None:
    ledger = record(record({}, ("c2", "c1"), B, NEW_JUDGE, 3), ("c1",), A, JUDGE, 1)
    text = exposure_to_json(ledger)
    assert text.endswith("\n")
    assert list(json.loads(text)) == ["cases", "version"]
    assert list(json.loads(text)["cases"]) == ["c1", "c2"]
    assert json.loads(text)["cases"]["c1"] == {
        "candidates": [A, B],
        "judges": [JUDGE, NEW_JUDGE],
        "rounds": [1, 3],
    }
    assert parse_exposure(text) == ledger
    assert exposure_to_json(parse_exposure(text)) == text


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"version": 2, "cases": {}}',
        '{"version": 1}',
        '{"version": 1, "cases": []}',
        '{"version": 1, "cases": {"c1": []}}',
        '{"version": 1, "cases": {"c1": {"candidates": [], "judges": []}}}',
        '{"version": 1, "cases": {"c1": {"candidates": [1], "judges": [], "rounds": []}}}',
        '{"version": 1, "cases": {"c1": {"candidates": [], "judges": [], "rounds": ["1"]}}}',
        '{"version": 1, "cases": {"c1": {"candidates": [], "judges": [], "rounds": [true]}}}',
    ],
)
def test_a_broken_ledger_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        parse_exposure(text)
