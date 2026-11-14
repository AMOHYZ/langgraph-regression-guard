"""exposure.json: which judge versions and candidates saw each gate case, across every round
(pure). It only ever grows: a new round adds to it and never starts it over. A case is named by a
hash of its inputs (gate_round.case_ids), since a run's number changes with the runs around it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass

VERSION = 1


@dataclass(frozen=True)
class CaseExposure:
    candidates: tuple[str, ...] = ()
    judges: tuple[str, ...] = ()  # JudgeLock.id
    rounds: tuple[int, ...] = ()


def entries(ledger: Mapping[str, CaseExposure]) -> int:
    """How much the ledger holds. Recording never lowers it, so a lower count is lost history."""
    return sum(len(c.candidates) + len(c.judges) + len(c.rounds) for c in ledger.values())


def record(
    ledger: Mapping[str, CaseExposure],
    cases: Iterable[str],
    candidate: str,
    judge: str,
    round_no: int,
) -> dict[str, CaseExposure]:
    """The ledger with this candidate, judge and round added to these cases. Every other case
    keeps what it has, so a case a later round drops isn't forgotten."""
    out = dict(ledger)
    for case in cases:
        seen = out.get(case, CaseExposure())
        out[case] = CaseExposure(
            tuple(sorted({*seen.candidates, candidate})),
            tuple(sorted({*seen.judges, judge})),
            tuple(sorted({*seen.rounds, round_no})),
        )
    return out


def _strings(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{where} must be a list of strings")
    return tuple(value)


def _rounds(value: object, where: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not all(
        isinstance(v, int) and not isinstance(v, bool) for v in value
    ):
        raise ValueError(f"{where} must be a list of whole numbers")
    return tuple(value)


def parse_exposure(text: str) -> dict[str, CaseExposure]:
    """The ledger in a file. ValueError if it isn't one."""
    raw: object = json.loads(text)
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        raise ValueError(f"not a version {VERSION} exposure ledger")
    cases = raw.get("cases")
    if not isinstance(cases, dict):
        raise ValueError("needs a 'cases' mapping")
    ledger = {}
    for case, seen in cases.items():
        fields = seen if isinstance(seen, dict) else {}
        ledger[case] = CaseExposure(
            _strings(fields.get("candidates"), f"case {case}: 'candidates'"),
            _strings(fields.get("judges"), f"case {case}: 'judges'"),
            _rounds(fields.get("rounds"), f"case {case}: 'rounds'"),
        )
    return ledger


def exposure_to_json(ledger: Mapping[str, CaseExposure]) -> str:
    cases = {case: asdict(seen) for case, seen in ledger.items()}
    return json.dumps({"version": VERSION, "cases": cases}, indent=2, sort_keys=True) + "\n"
