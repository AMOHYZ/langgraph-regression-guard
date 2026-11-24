"""T155-A: adoption path state machine (distinct from ProposeState).

eligible → approved → baseline_updated → merged → deployed.
Change-record stub only; bind/approve CLI is T155-B.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, get_args

AdoptionState = Literal["eligible", "approved", "baseline_updated", "merged", "deployed"]

ADOPTION_ORDER: tuple[AdoptionState, ...] = get_args(AdoptionState)


class IllegalTransition(ValueError):
    """A transition that skips or reverses the adoption path."""


def transition(current: AdoptionState, nxt: AdoptionState) -> AdoptionState:
    """Advance one step along ADOPTION_ORDER; anything else raises IllegalTransition."""
    try:
        i = ADOPTION_ORDER.index(current)
        j = ADOPTION_ORDER.index(nxt)
    except ValueError as exc:
        raise IllegalTransition(f"unknown adoption state in {current!r} → {nxt!r}") from exc
    if j != i + 1:
        raise IllegalTransition(f"illegal adoption transition {current!r} → {nxt!r}")
    return nxt


@dataclass(frozen=True)
class ChangeRecord:
    """Stub change record for T155-A; T155-B binds SHAs/hashes and writes on each transition."""

    pr: int
    base_sha: str
    candidate_sha: str
    state: AdoptionState = "eligible"
    history: tuple[AdoptionState, ...] = field(default_factory=lambda: ("eligible",))

    @classmethod
    def new(cls, *, pr: int, base_sha: str, candidate_sha: str) -> ChangeRecord:
        return cls(pr=pr, base_sha=base_sha, candidate_sha=candidate_sha)

    def advance(self, nxt: AdoptionState) -> ChangeRecord:
        state = transition(self.state, nxt)
        return ChangeRecord(
            pr=self.pr,
            base_sha=self.base_sha,
            candidate_sha=self.candidate_sha,
            state=state,
            history=(*self.history, state),
        )
