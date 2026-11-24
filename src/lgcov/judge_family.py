"""T127: gate_judge.family vs optimised_model.family — warn only, never fail."""

from __future__ import annotations


def family_mismatch_warning(gate_family: str | None, optimised_family: str | None) -> str | None:
    """⚠️ line when both families are set and differ; None otherwise. Never a hard failure."""
    if gate_family is None or optimised_family is None:
        return None
    if gate_family == optimised_family:
        return None
    return (
        f"⚠️ gate_judge.family ({gate_family!r}) differs from optimised_model.family "
        f"({optimised_family!r}): warn only — the gate does not fail on this."
    )
