"""T124: case pools stay apart — id overlap, judge-fit promotion, near-duplicate text."""

from __future__ import annotations

import pytest

from lgcov.exposure import CaseExposure
from lgcov.gate_round import JudgeLock
from lgcov.pools import (
    CaseRecord,
    LineageEntry,
    candidates_seen,
    check_pools,
    normalise_case_text,
    parse_lineage,
    text_hash,
)

JUDGE = JudgeLock("jev-1", "0.1.0", "c" * 64)


def _case(case_id: str, text: str, pool: str) -> CaseRecord:
    return CaseRecord(case_id, text, pool)


def test_case_id_overlap_is_refused() -> None:
    cases = (
        _case("shared", "Where is my order?", "optimiser"),
        _case("shared", "Where is my order?", "gate"),
    )
    problems = check_pools(cases, {}, JUDGE)
    assert any("shared" in p and "optimiser" in p and "gate" in p for p in problems)


def test_refuses_gate_case_current_judge_was_fitted_on() -> None:
    cases = (_case("fit-me", "Track this package", "gate"),)
    lineage = {"fit-me": LineageEntry(pool="gate", judges_fitted=(JUDGE.id,))}
    problems = check_pools(cases, lineage, JUDGE)
    assert any("fit-me" in p and JUDGE.id in p and "fitted" in p for p in problems)


def test_no_locked_round_cannot_check_is_not_a_pass() -> None:
    cases = (_case("only-gate", "Track this package", "gate"),)
    problems = check_pools(cases, {}, current_judge=None)
    assert problems
    assert any("cannot check" in p for p in problems)


def test_refuses_duplicate_normalised_text_hash() -> None:
    cases = (
        _case("a", "Where is my order?", "optimiser"),
        _case("b", "  WHERE   is my ORDER?  ", "gate"),
    )
    problems = check_pools(cases, {}, JUDGE)
    assert any("normalised text" in p and "a" in p and "b" in p for p in problems)
    assert text_hash("Where is my order?") == text_hash("  WHERE   is my ORDER?  ")
    assert text_hash("Where is my order?").startswith("v1:")


def test_clean_pools_pass() -> None:
    cases = (
        _case("opt-1", "Where is my order?", "optimiser"),
        _case("gate-1", "What is the refund policy?", "gate"),
    )
    lineage = {
        "opt-1": LineageEntry(pool="optimiser", judges_fitted=(JUDGE.id,)),
        "gate-1": LineageEntry(pool="gate", judges_fitted=()),
    }
    assert check_pools(cases, lineage, JUDGE) == []


def test_normalise_case_text_is_nfkc_casefold_whitespace_trim() -> None:
    # NFKC: fullwidth digits; casefold; collapse whitespace; trim. Input text only.
    assert normalise_case_text("  Order\u3000１２３  ") == "order 123"
    assert normalise_case_text("A\n\tB") == "a b"


def test_elapsed_rounds_do_not_clear_judges_fitted() -> None:
    """A case stays fitted even if exposure shows later rounds — rounds do not reset lineage."""
    cases = (_case("old-fit", "Refund my order", "gate"),)
    lineage = {"old-fit": LineageEntry(pool="gate", judges_fitted=(JUDGE.id,))}
    problems = check_pools(cases, lineage, JUDGE)
    assert any("old-fit" in p for p in problems)


def test_lineage_refuses_candidates_seen_and_reads_them_from_exposure() -> None:
    with pytest.raises(ValueError, match="exposure.json"):
        parse_lineage(
            '{"version": 1, "cases": {"c1": {"pool": "gate", "judges_fitted": [], '
            '"candidates_seen": ["a"]}}}'
        )
    ledger = {"c1": CaseExposure(candidates=("a" * 40,), judges=(), rounds=(1,))}
    assert candidates_seen(ledger, "c1") == ("a" * 40,)
    assert candidates_seen(ledger, "missing") == ()
