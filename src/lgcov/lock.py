"""T114: the two commits a gate run compares, recorded at its start. A run whose base or
candidate checkout moves before it ends gives no verdict: INFRA_ERROR (pure)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Commits:
    base: str | None  # the base checkout's HEAD; None: none given, or not a git checkout
    candidate: str | None  # HEAD of the checkout under test; None: not a git checkout
    dirty: bool = False  # a locked side had uncommitted changes, which the lock can't see


def _short(sha: str | None) -> str:
    return f"`{sha[:7]}`" if sha else "unreadable"


def moved(start: Commits, end: Commits) -> str:
    """'' when neither side moved; else why the run gives no verdict."""
    sides = [
        f"the {side} was {_short(was)} at the start and is {_short(now)} now"
        for side, was, now in (
            ("base", start.base, end.base),
            ("candidate", start.candidate, end.candidate),
        )
        if was != now
    ]
    if not sides:
        return ""
    return (
        f"Locked commits moved during the run: {'; '.join(sides)}. The run's verdicts would "
        "mix two versions of the code, so it gives none; re-run on one commit."
    )


def locked_line(c: Commits) -> str:
    """The report's record of the commits this run compared."""
    pairs = (("base", c.base), ("candidate", c.candidate))
    sides = [f"{side} {_short(sha)}" for side, sha in pairs if sha]
    if not sides:
        return "🔓 Not locked: not a git checkout."
    line = f"🔒 Locked for this run: {', '.join(sides)}."
    return f"{line} ⚠️ uncommitted changes, not covered by the lock" if c.dirty else line
