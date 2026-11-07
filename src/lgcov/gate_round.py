"""lgcov-round.yaml: one round of gate runs, fixed before its candidates see the gate's cases
(pure). `lgcov round new` writes it and `lgcov guard` compares it with what the gate would use now.
docs/round.md has the format and what it doesn't stop.

The digest catches a hand edit. It is not a signature: everything in the file is public, so anyone
who can edit it can recompute the digest.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

import yaml

from lgcov.gate_result import file_hash
from lgcov.taxonomy import KPISpec

VERSION = 1
HEADER = (
    "# Locked by `lgcov round new`. To change the plan, edit only what a person writes\n"
    "# (docs/round.md) and run it again; a hand edit alone makes `lgcov guard` exit 2.\n"
)
StopValue = bool | int | float | str

_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_KEYS = (
    "version",
    "round",
    "gate_cases",
    "judge",
    "kpis",
    "alpha",
    "power",
    "delta_1",
    "prior_rho",
    "k_gate",
    "candidates",
    "stop_rules",
    "exposure_floor",
    "digest",
)


@dataclass(frozen=True)
class JudgeLock:
    model: str | None  # lgcov.yaml's jev_model; None: no KPI in the taxonomy needs one
    version: str  # lgcov's version, as lgcov holds the judges' code
    prompt_sha256: str  # prompt_hash

    @property
    def id(self) -> str:
        """What exposure.json calls this judge version."""
        return f"{self.model or 'no model'}@{self.version}/{self.prompt_sha256[:12]}"


@dataclass(frozen=True)
class KpiLock:
    name: str
    tags: tuple[str, ...]
    margin: float


@dataclass(frozen=True)
class GateNow:
    """What the gate would use today, read fresh."""

    case_ids: tuple[str, ...]
    judge: JudgeLock
    margins: dict[str, float]  # every KPI in the taxonomy, in its order
    alpha: float
    power: float
    k_gate: int
    unpinned: tuple[str, ...] = ()  # jev KPIs whose model can change with no hash moving


@dataclass(frozen=True)
class Draft:
    """What a person writes in the round file; `lgcov round new` computes the rest."""

    gate_cases_path: str
    tags: dict[str, tuple[str, ...]]  # KPI name -> its tags
    delta_1: float
    prior_rho: float
    candidates: tuple[str, ...]
    stop_rules: dict[str, StopValue]
    previous_round: int = 0
    previous_floor: int = 0


@dataclass(frozen=True)
class Round:
    round: int
    gate_cases_path: str
    case_set_sha256: str
    judge: JudgeLock
    kpis: tuple[KpiLock, ...]
    alpha: float
    power: float
    delta_1: float  # Δ₁
    prior_rho: float  # prior ρ
    k_gate: int  # the blocking KPIs, as docs/gate.md counts them
    candidates: tuple[str, ...]
    stop_rules: dict[str, StopValue]
    exposure_floor: int  # the ledger's entries when this round started
    digest: str = ""  # as written in the file; round_digest says what it should be


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def case_ids(baseline: object) -> tuple[str, ...]:
    """One id per run in a parsed baseline.json, from its inputs alone: a re-baseline changes a
    run's outputs, not the case."""
    runs = baseline.get("runs") if isinstance(baseline, dict) else None
    if not isinstance(runs, list) or not runs:
        raise ValueError("needs a non-empty 'runs' list")
    ids = []
    for i, run in enumerate(runs):
        if not isinstance(run, dict) or "inputs" not in run:
            raise ValueError(f"run {i} has no 'inputs'")
        ids.append(file_hash(_canonical(run["inputs"]))[:16])
    return tuple(ids)


def case_set_hash(ids: Iterable[str]) -> str:
    """The set's hash, in any order, with each case counted."""
    return file_hash(_canonical(sorted(ids)))


def prompt_hash(specs: Iterable[KPISpec]) -> str:
    """Over what decides each KPI's verdict: what its judge is asked and what an answer scores.
    Not its description, which no judge is sent, or its margin, which a round locks by itself."""
    body = [
        {
            "name": s.name,
            "judge": s.judge,
            "node": s.node,
            "rubric": s.rubric,
            "labels": s.labels,
            "score_map": s.score_map,
            "label_hints": s.label_hints,
            "check": s.check,
        }
        for s in sorted(specs, key=lambda s: s.name)
    ]
    return file_hash(_canonical(body))


def is_full_sha(value: str) -> bool:
    """A whole commit SHA, as git prints it. A short one could match another commit later."""
    return _SHA.fullmatch(value) is not None


def _mapping(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError(f"{where} must be a mapping")
    return dict(value)


def _check_keys(d: Mapping[str, object], valid: tuple[str, ...], where: str) -> None:
    unknown = sorted(set(d) - set(valid))
    if unknown:
        raise ValueError(f"{where}: unknown keys {unknown}, valid keys: {list(valid)}")


def _required(d: Mapping[str, object], key: str) -> object:
    if key not in d:
        raise ValueError(f"'{key}' is required")
    return d[key]


def _str(value: object, key: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"'{key}' must be a non-empty string")
    return value


def _hash(value: object, key: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"'{key}' must be a sha256 hash; `lgcov round new` writes it")
    return value


def _number(value: object, key: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not low <= value <= high:
        raise ValueError(f"'{key}' must be a number in {low:g}..{high:g}")
    return float(value)


def _probability(value: object, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value < 1:
        raise ValueError(f"'{key}' must be a number between 0 and 1")
    return float(value)


def _count(value: object, key: str, low: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise ValueError(f"'{key}' must be a whole number, at least {low}")
    return value


def _candidates(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(v, str) and is_full_sha(v) for v in value)
    ):
        raise ValueError(
            "'candidates' must be a non-empty list of full commit SHAs, in lowercase hex"
        )
    if len(set(value)) != len(value):
        raise ValueError("'candidates' lists a commit twice")
    return tuple(value)


def _stop_rules(value: object) -> dict[str, StopValue]:
    rules: dict[str, StopValue] = {}
    for name, rule in _mapping(value, "'stop_rules'").items():
        if not isinstance(rule, bool | int | float | str):
            raise ValueError(f"'stop_rules': '{name}' must be a number, a string or true/false")
        rules[name] = rule
    return rules


def _kpi_entries(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("'kpis' must be a list")
    kpis = [_mapping(k, "each of 'kpis'") for k in value]
    for kpi in kpis:
        _check_keys(kpi, ("name", "tags", "margin"), "'kpis'")
    names = [_str(_required(kpi, "name"), "name") for kpi in kpis]
    if len(set(names)) != len(names):
        raise ValueError("'kpis' lists a KPI twice")
    return kpis


def _tag_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("a KPI's 'tags' must be a list of strings")
    return tuple(value)


def parse_draft(raw: object) -> Draft:
    """What a person writes, from a draft or a locked round. The computed fields aren't read here,
    other than the round number and the exposure floor the next round carries on from."""
    doc = _mapping(raw, "the round file")
    _check_keys(doc, _KEYS, "the round file")
    if doc.get("version") != VERSION:
        raise ValueError(f"'version' must be {VERSION}, got {doc.get('version')!r}")
    cases = _mapping(_required(doc, "gate_cases"), "'gate_cases'")
    _check_keys(cases, ("path", "sha256"), "'gate_cases'")
    kpis = _kpi_entries(doc.get("kpis", []))
    return Draft(
        gate_cases_path=_str(_required(cases, "path"), "gate_cases.path"),
        tags={str(k["name"]): _tag_list(k.get("tags", [])) for k in kpis},
        delta_1=_number(_required(doc, "delta_1"), "delta_1", -1, 1),
        prior_rho=_number(_required(doc, "prior_rho"), "prior_rho", -1, 1),
        candidates=_candidates(_required(doc, "candidates")),
        stop_rules=_stop_rules(_required(doc, "stop_rules")),
        previous_round=_count(doc["round"], "round", 1) if "round" in doc else 0,
        previous_floor=_count(doc.get("exposure_floor", 0), "exposure_floor", 0),
    )


def _judge(value: object) -> JudgeLock:
    judge = _mapping(value, "'judge'")
    _check_keys(judge, ("model", "version", "prompt_sha256"), "'judge'")
    model = judge.get("model")
    return JudgeLock(
        None if model is None else _str(model, "judge.model"),
        _str(_required(judge, "version"), "judge.version"),
        _hash(_required(judge, "prompt_sha256"), "judge.prompt_sha256"),
    )


def parse_round(raw: object) -> Round:
    """A locked round. A hand edit still parses, so round_problems can name it with the rest."""
    draft = parse_draft(raw)
    doc = _mapping(raw, "the round file")
    cases = _mapping(doc["gate_cases"], "'gate_cases'")
    return Round(
        round=_count(_required(doc, "round"), "round", 1),
        gate_cases_path=draft.gate_cases_path,
        case_set_sha256=_hash(_required(cases, "sha256"), "gate_cases.sha256"),
        judge=_judge(_required(doc, "judge")),
        kpis=tuple(
            KpiLock(
                str(k["name"]),
                _tag_list(k.get("tags", [])),
                _number(_required(k, "margin"), "margin", 0, 1),
            )
            for k in _kpi_entries(_required(doc, "kpis"))
        ),
        alpha=_probability(_required(doc, "alpha"), "alpha"),
        power=_probability(_required(doc, "power"), "power"),
        delta_1=draft.delta_1,
        prior_rho=draft.prior_rho,
        k_gate=_count(_required(doc, "k_gate"), "k_gate", 0),
        candidates=draft.candidates,
        stop_rules=draft.stop_rules,
        exposure_floor=_count(_required(doc, "exposure_floor"), "exposure_floor", 0),
        digest=_hash(_required(doc, "digest"), "digest"),
    )


def _body(r: Round) -> dict[str, object]:
    return {
        "version": VERSION,
        "round": r.round,
        "gate_cases": {"path": r.gate_cases_path, "sha256": r.case_set_sha256},
        "judge": {
            "model": r.judge.model,
            "version": r.judge.version,
            "prompt_sha256": r.judge.prompt_sha256,
        },
        "kpis": [{"name": k.name, "tags": list(k.tags), "margin": k.margin} for k in r.kpis],
        "alpha": r.alpha,
        "power": r.power,
        "delta_1": r.delta_1,
        "prior_rho": r.prior_rho,
        "k_gate": r.k_gate,
        "candidates": list(r.candidates),
        "stop_rules": r.stop_rules,
        "exposure_floor": r.exposure_floor,
    }


def round_digest(r: Round) -> str:
    """Over every field but the digest itself."""
    return file_hash(_canonical(_body(r)))


def round_to_yaml(r: Round) -> str:
    doc = {**_body(r), "digest": r.digest}
    return HEADER + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


def _lost(exposure_entries: int, floor: int, whose: str) -> str:
    return (
        f"exposure history lost: the ledger has {exposure_entries} entries and {whose} started "
        f"with {floor}; restore it from git"
    )


def lock_round(draft: Draft, now: GateNow, exposure_entries: int) -> Round:
    """The next round: what the person wrote, and the gate's settings as they stand. ValueError
    if it can't be locked. A new round can't start over a ledger that lost history."""
    if exposure_entries < draft.previous_floor:
        raise ValueError(_lost(exposure_entries, draft.previous_floor, "the last round"))
    if now.unpinned:
        raise ValueError(
            "pin jev_model in lgcov.yaml before locking a round, as an unpinned or latest model "
            f"can change with no hash moving: {', '.join(now.unpinned)}"
        )
    unknown = sorted(set(draft.tags) - set(now.margins))
    if unknown:
        raise ValueError(
            f"'kpis' names {unknown}, which the taxonomy doesn't have (it has {list(now.margins)})"
        )
    locked = Round(
        round=draft.previous_round + 1,
        gate_cases_path=draft.gate_cases_path,
        case_set_sha256=case_set_hash(now.case_ids),
        judge=now.judge,
        kpis=tuple(
            KpiLock(name, draft.tags.get(name, ()), margin) for name, margin in now.margins.items()
        ),
        alpha=now.alpha,
        power=now.power,
        delta_1=draft.delta_1,
        prior_rho=draft.prior_rho,
        k_gate=now.k_gate,
        candidates=draft.candidates,
        stop_rules=draft.stop_rules,
        exposure_floor=exposure_entries,
    )
    return replace(locked, digest=round_digest(locked))


def _short(sha: str) -> str:
    return f"`{sha[:7]}`"


def _shown(value: object) -> str:
    if value is None:
        return "none"
    return value[:12] if isinstance(value, str) and _SHA256.fullmatch(value) else str(value)


def round_problems(
    locked: Round, now: GateNow, candidate: str | None, exposure_entries: int
) -> list[str]:
    """Everything that stops this round covering this run; empty when nothing does."""
    problems = []
    if locked.digest != round_digest(locked):
        problems.append(
            "the round file was edited after `lgcov round new`: its digest doesn't match"
        )
    rows: tuple[tuple[str, object, object], ...] = (
        ("gate case set", locked.case_set_sha256, case_set_hash(now.case_ids)),
        ("judge model", locked.judge.model, now.judge.model),
        ("judge version", locked.judge.version, now.judge.version),
        ("judge prompt hash", locked.judge.prompt_sha256, now.judge.prompt_sha256),
        ("alpha", locked.alpha, now.alpha),
        ("power", locked.power, now.power),
        ("K_gate", locked.k_gate, now.k_gate),
    )
    problems += [
        f"{what}: locked {_shown(was)}, now {_shown(is_now)}"
        for what, was, is_now in rows
        if was != is_now
    ]
    for kpi in locked.kpis:
        if kpi.name not in now.margins:
            problems.append(f"KPI {kpi.name}: in the round, not in the taxonomy")
        elif kpi.margin != now.margins[kpi.name]:
            problems.append(
                f"KPI {kpi.name} margin: locked {kpi.margin}, now {now.margins[kpi.name]}"
            )
    in_round = {kpi.name for kpi in locked.kpis}
    problems += [
        f"KPI {name}: in the taxonomy, not in the round"
        for name in now.margins
        if name not in in_round
    ]
    if candidate is None:
        problems.append(
            "no candidate to check: this is no git checkout or pull_request event; pass --candidate"
        )
    elif candidate not in locked.candidates:
        names = ", ".join(_short(c) for c in locked.candidates)
        problems.append(
            f"candidate {_short(candidate)} is not locked in round {locked.round} (locked: {names})"
        )
    if exposure_entries < locked.exposure_floor:
        problems.append(_lost(exposure_entries, locked.exposure_floor, "this round"))
    return problems
