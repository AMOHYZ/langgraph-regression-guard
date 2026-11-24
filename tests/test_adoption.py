"""T155-A: AdoptionState enum and legal transitions (distinct from ProposeState)."""

from __future__ import annotations

from typing import get_args

import pytest

from lgcov.adoption import (
    ADOPTION_ORDER,
    AdoptionState,
    ChangeRecord,
    IllegalTransition,
    transition,
)
from lgcov.propose import ProposeState


def test_adoption_states_are_the_ruled_sequence() -> None:
    assert ADOPTION_ORDER == (
        "eligible",
        "approved",
        "baseline_updated",
        "merged",
        "deployed",
    )
    assert set(ADOPTION_ORDER) == set(get_args(AdoptionState))


def test_propose_state_is_not_adoption_state() -> None:
    propose = frozenset(get_args(ProposeState))
    adoption = frozenset(get_args(AdoptionState))
    # Shared word "eligible" only; adoption's later steps are not propose states.
    assert adoption - {"eligible"} == {"approved", "baseline_updated", "merged", "deployed"}
    assert "approved" not in propose
    assert "blocked" not in adoption


def test_legal_forward_transitions() -> None:
    state: AdoptionState = "eligible"
    for nxt in ("approved", "baseline_updated", "merged", "deployed"):
        state = transition(state, nxt)
        assert state == nxt


def test_illegal_skip_or_backward_raises() -> None:
    with pytest.raises(IllegalTransition, match="eligible.*baseline_updated"):
        transition("eligible", "baseline_updated")
    with pytest.raises(IllegalTransition, match="approved.*eligible"):
        transition("approved", "eligible")
    with pytest.raises(IllegalTransition, match="deployed"):
        transition("deployed", "merged")


def test_change_record_stub_tracks_state() -> None:
    record = ChangeRecord.new(pr=1, base_sha="a" * 40, candidate_sha="b" * 40)
    assert record.state == "eligible"
    record = record.advance("approved")
    assert record.state == "approved"
    assert record.history == ("eligible", "approved")
