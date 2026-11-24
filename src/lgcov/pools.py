"""T124: case pools (optimiser vs gate) and lineage — keep them apart (pure).

`lineage.json` holds pool membership and `judges_fitted` only. Which candidates saw a case
is read from `exposure.json` (never copied into lineage). Elapsed rounds do not clear a fit:
a case the current judge was fitted on still cannot enter the gate pool.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from lgcov.exposure import CaseExposure
from lgcov.gate_round import JudgeLock, parse_round

VERSION = 1
DEFAULT_OPTIMISER = "cases/optimiser"
DEFAULT_GATE = "cases/gate"
DEFAULT_LINEAGE = "lineage.json"
DEFAULT_ROUND = "lgcov-round.yaml"
POOLS = ("optimiser", "gate")
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class CaseRecord:
    id: str
    text: str  # input text only; used for near-exact duplicate detection
    pool: str  # optimiser | gate
    control: bool = False  # T123: immutable negative control
    immutability_rationale: str = ""  # required when control is true


@dataclass(frozen=True)
class LineageEntry:
    pool: str
    judges_fitted: tuple[str, ...] = ()


def normalise_case_text(text: str) -> str:
    """NFKC, casefold, collapse whitespace, trim. Input text only — not paraphrases."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()


def text_hash(text: str) -> str:
    """`v1:` plus sha256 of normalised input text. Exact / near-exact duplicates only."""
    digest = hashlib.sha256(normalise_case_text(text).encode()).hexdigest()
    return f"v1:{digest}"


def check_pools(
    cases: Sequence[CaseRecord],
    lineage: Mapping[str, LineageEntry],
    current_judge: JudgeLock | None,
    *,
    human_pass_case_ids: frozenset[str] | None = None,
) -> list[str]:
    """Every problem that stops the pools being clean; empty when nothing does.

    (a) same case id in two pools
    (b) a gate case the current judge was fitted on (no locked round → cannot check)
    (c) two case ids sharing a normalised-text hash (exact / near-exact, not paraphrase)
    (d) T123: control with no immutability_rationale
    (e) T123: control with a recorded human-labelled pass (hard-but-solvable; refuse for v0)
    """
    problems: list[str] = []
    by_id: dict[str, list[str]] = {}
    for case in cases:
        by_id.setdefault(case.id, []).append(case.pool)
    for case_id, pools in sorted(by_id.items()):
        distinct = sorted(set(pools))
        if len(distinct) > 1:
            problems.append(
                f"case {case_id} is in both cases/{distinct[0]} and cases/{distinct[1]}"
            )

    if current_judge is None:
        problems.append(
            "cannot check: no locked gate round on the base "
            "(current judge is the locked round's judge)"
        )
    else:
        for case in cases:
            if case.pool != "gate":
                continue
            fitted = lineage.get(case.id, LineageEntry(case.pool)).judges_fitted
            if current_judge.id in fitted:
                problems.append(
                    f"case {case.id} cannot enter the gate pool: current judge "
                    f"{current_judge.id} was fitted on it"
                )

    by_hash: dict[str, list[str]] = {}
    for case in cases:
        by_hash.setdefault(text_hash(case.text), []).append(case.id)
    for digest, ids in sorted(by_hash.items()):
        unique = sorted(set(ids))
        if len(unique) > 1:
            problems.append(
                f"cases {unique[0]} and {unique[1]} share normalised text hash {digest}"
            )

    human_passes = human_pass_case_ids or frozenset()
    for case in cases:
        if not case.control:
            continue
        if not case.immutability_rationale.strip():
            problems.append(
                f"case {case.id}: control: true requires a non-empty immutability_rationale"
            )
        if case.id in human_passes:
            problems.append(
                f"case {case.id}: control has a recorded human-labelled pass "
                f"(hard-but-solvable controls are refused in v0)"
            )
    return problems


def pass_rate_excluding_controls(
    cases: Sequence[CaseRecord], passed_ids: frozenset[str]
) -> tuple[int, int]:
    """(passed, total) over non-control cases only — controls stay out of the denominator."""
    eligible = [c for c in cases if not c.control]
    total = len(eligible)
    passed = sum(1 for c in eligible if c.id in passed_ids)
    return passed, total


def parse_lineage(text: str) -> dict[str, LineageEntry]:
    """The lineage file. ValueError if it isn't one. No candidates_seen — that stays in exposure."""
    raw: object = json.loads(text)
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        raise ValueError(f"not a version {VERSION} lineage file")
    cases = raw.get("cases")
    if not isinstance(cases, dict):
        raise ValueError("needs a 'cases' mapping")
    out: dict[str, LineageEntry] = {}
    for case_id, entry in cases.items():
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("each case id must be a non-empty string")
        if not isinstance(entry, dict):
            raise ValueError(f"case {case_id}: needs an object")
        pool = entry.get("pool")
        if pool not in POOLS:
            raise ValueError(f"case {case_id}: pool must be optimiser or gate")
        fitted = entry.get("judges_fitted", [])
        if not isinstance(fitted, list) or not all(isinstance(j, str) for j in fitted):
            raise ValueError(f"case {case_id}: judges_fitted must be a list of strings")
        if "candidates_seen" in entry:
            raise ValueError(
                f"case {case_id}: candidates_seen belongs in exposure.json, not lineage"
            )
        out[case_id] = LineageEntry(pool, tuple(fitted))
    return out


def lineage_to_json(lineage: Mapping[str, LineageEntry]) -> str:
    cases = {
        case_id: {"pool": entry.pool, "judges_fitted": list(entry.judges_fitted)}
        for case_id, entry in lineage.items()
    }
    return json.dumps({"version": VERSION, "cases": cases}, indent=2, sort_keys=True) + "\n"


def _case_fields(raw: object, where: str) -> CaseRecord:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: needs a JSON object with id and text")
    case_id, text = raw.get("id"), raw.get("text")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError(f"{where}: needs a non-empty string 'id'")
    if not isinstance(text, str):
        raise ValueError(f"{where}: needs a string 'text' (input text only)")
    control = raw.get("control", False)
    if not isinstance(control, bool):
        raise ValueError(f"{where}: 'control' must be a boolean when set")
    rationale = raw.get("immutability_rationale", "")
    if rationale is None:
        rationale = ""
    if not isinstance(rationale, str):
        raise ValueError(f"{where}: 'immutability_rationale' must be a string when set")
    # pool filled by caller
    return CaseRecord(case_id, text, "", control=control, immutability_rationale=rationale)


def load_pool_dir(path: Path, pool: str) -> list[CaseRecord]:
    """Every `*.json` case file under path. Missing folder → no cases."""
    if not path.is_dir():
        return []
    cases: list[CaseRecord] = []
    for file in sorted(path.glob("*.json")):
        try:
            partial = _case_fields(json.loads(file.read_text(encoding="utf-8")), str(file))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{file}: {exc}") from None
        cases.append(
            CaseRecord(
                partial.id,
                partial.text,
                pool,
                control=partial.control,
                immutability_rationale=partial.immutability_rationale,
            )
        )
    return cases


def load_cases(
    optimiser: Path = Path(DEFAULT_OPTIMISER), gate: Path = Path(DEFAULT_GATE)
) -> list[CaseRecord]:
    return [*load_pool_dir(optimiser, "optimiser"), *load_pool_dir(gate, "gate")]


def pools_paths_edited(
    pr_root: Path,
    base_root: Path,
    *,
    optimiser: str = DEFAULT_OPTIMISER,
    gate: str = DEFAULT_GATE,
    lineage: str = DEFAULT_LINEAGE,
) -> bool:
    """True when the PR changed lineage.json or a pool folder versus the base checkout."""
    for rel in (lineage, optimiser, gate):
        if _tree_bytes(pr_root / rel) != _tree_bytes(base_root / rel):
            return True
    return False


def _tree_bytes(path: Path) -> dict[str, bytes]:
    if path.is_file():
        return {path.name: path.read_bytes()}
    if not path.is_dir():
        return {}
    out: dict[str, bytes] = {}
    for file in sorted(path.rglob("*")):
        if file.is_file():
            out[file.relative_to(path).as_posix()] = file.read_bytes()
    return out


def current_judge_from_round(text: str | None) -> JudgeLock | None:
    """The locked round's judge, or None when there is no locked round (cannot check)."""
    if text is None:
        return None
    try:
        return parse_round(yaml.safe_load(text)).judge
    except (ValueError, TypeError, yaml.YAMLError):
        return None


def candidates_seen(exposure: Mapping[str, CaseExposure], case_id: str) -> tuple[str, ...]:
    """Candidates that saw this case, from exposure.json — never stored in lineage."""
    seen = exposure.get(case_id)
    return seen.candidates if seen is not None else ()


def lineage_pool_mismatches(
    cases: Iterable[CaseRecord], lineage: Mapping[str, LineageEntry]
) -> list[str]:
    """Refuse when lineage.pool disagrees with the folder a case file lives in."""
    return [
        f"case {case.id}: lineage says pool {entry.pool}, file is under cases/{case.pool}"
        for case in cases
        if (entry := lineage.get(case.id)) is not None and entry.pool != case.pool
    ]
