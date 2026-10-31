"""T075: taxonomy.yaml -> KPI objects -> one judge per KPI."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from lgcov.code_judges import code_judge
from lgcov.judges import KPI, Judge

VERSION = 1
JUDGE_KINDS = ("jev", "code", "fake")
BINARY_LABELS = ("pass", "fail")


@dataclass(frozen=True)
class FailureMode:
    id: str
    description: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class KPISpec:
    """A KPI as written in the file, plus where and how it is judged."""

    name: str
    failure_mode: str
    node: str  # graph node the judge reads from the cassette, or "final"
    judge: str  # one of JUDGE_KINDS
    description: str
    rubric: str
    labels: tuple[str, ...] = BINARY_LABELS
    score_map: dict[str, float] = field(default_factory=lambda: {"pass": 1.0, "fail": 0.0})
    label_hints: dict[str, str] = field(default_factory=dict)
    check: dict[str, str] | None = None  # only for judge: code (T072)
    margin: float | None = None  # T091: non-inferiority margin δ; None = gate.margin

    def to_kpi(self) -> KPI:
        return KPI(
            name=self.name,
            description=self.description,
            labels=self.labels,
            rubric=self.rubric,
            score_map=self.score_map,
            label_hints=self.label_hints,
        )


@dataclass(frozen=True)
class Taxonomy:
    version: int
    system: str
    failure_modes: tuple[FailureMode, ...]
    kpi_specs: tuple[KPISpec, ...]

    def kpis(self) -> list[KPI]:
        return [spec.to_kpi() for spec in self.kpi_specs]


def _str(d: Mapping[str, object], key: str, where: str) -> str:
    value = d.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: '{key}' must be a non-empty string")
    return value


def _str_list(value: object, key: str, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{where}: '{key}' must be a list of strings")
    return tuple(value)


def _mapping(value: object, key: str, where: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError(f"{where}: '{key}' must be a mapping")
    return dict(value)


def _failure_mode(raw: object, index: int) -> FailureMode:
    where = f"failure_modes[{index}]"
    d = _mapping(raw, "entry", where)
    return FailureMode(
        id=_str(d, "id", where),
        description=_str(d, "description", where),
        evidence=_str_list(d.get("evidence", []), "evidence", where),
    )


def _kpi_spec(raw: object, index: int, mode_ids: set[str]) -> KPISpec:
    d = _mapping(raw, "entry", f"kpis[{index}]")
    name = _str(d, "name", f"kpis[{index}]")
    where = f"KPI '{name}'"
    failure_mode = _str(d, "failure_mode", where)
    if failure_mode not in mode_ids:
        raise ValueError(f"{where}: unknown failure_mode '{failure_mode}'")
    judge = _str(d, "judge", where)
    if judge not in JUDGE_KINDS:
        raise ValueError(f"{where}: unknown judge '{judge}', expected one of {JUDGE_KINDS}")
    labels = _str_list(d.get("labels", list(BINARY_LABELS)), "labels", where)
    if "score_map" in d:
        score_map: dict[str, float] = {}
        for label, score in _mapping(d["score_map"], "score_map", where).items():
            if isinstance(score, bool) or not isinstance(score, int | float):
                raise ValueError(f"{where}: score_map['{label}'] must be a number")
            score_map[label] = float(score)
    elif labels == BINARY_LABELS:
        score_map = {"pass": 1.0, "fail": 0.0}
    else:
        raise ValueError(f"{where}: custom labels {labels} need a score_map")
    hints = _mapping(d.get("label_hints", {}), "label_hints", where)
    if not set(hints) <= set(labels):
        extra = sorted(set(hints) - set(labels))
        raise ValueError(f"{where}: label_hints keys {extra} not in labels {labels}")
    check = _mapping(d["check"], "check", where) if "check" in d else None
    margin: float | None = None
    if "margin" in d:
        value = d["margin"]
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
            raise ValueError(f"{where}: 'margin' must be a number in 0..1")
        margin = float(value)
    spec = KPISpec(
        name=name,
        failure_mode=failure_mode,
        node=_str(d, "node", where),
        judge=judge,
        description=_str(d, "description", where),
        rubric=_str(d, "rubric", where),
        labels=labels,
        score_map=score_map,
        label_hints={k: str(v) for k, v in hints.items()},
        check={k: str(v) for k, v in check.items()} if check is not None else None,
        margin=margin,
    )
    spec.to_kpi()  # KPI's own label/score checks, at load time rather than build time
    if judge == "code":
        try:
            code_judge(spec.to_kpi(), spec.check or {})
        except ValueError as exc:
            raise ValueError(f"{where}: {exc}") from None
    return spec


def parse_taxonomy(raw: object) -> Taxonomy:
    """Validate a parsed YAML document (pure; load_taxonomy does the file I/O)."""
    doc = _mapping(raw, "top level", "taxonomy")
    version = doc.get("version")
    if version != VERSION:
        raise ValueError(f"taxonomy: unsupported version {version!r}, expected {VERSION}")
    modes_raw = doc.get("failure_modes")
    kpis_raw = doc.get("kpis")
    if not isinstance(modes_raw, list) or not isinstance(kpis_raw, list):
        raise ValueError("taxonomy: 'failure_modes' and 'kpis' must be lists")
    modes = tuple(_failure_mode(m, i) for i, m in enumerate(modes_raw))
    mode_ids = {m.id for m in modes}
    specs: list[KPISpec] = []
    for i, k in enumerate(kpis_raw):
        spec = _kpi_spec(k, i, mode_ids)
        if any(s.name == spec.name for s in specs):
            raise ValueError(f"KPI '{spec.name}': duplicate KPI name")
        specs.append(spec)
    return Taxonomy(VERSION, _str(doc, "system", "taxonomy"), modes, tuple(specs))


def load_taxonomy(path: Path | str) -> Taxonomy:
    text = Path(path).read_text(encoding="utf-8")
    try:
        raw: object = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: malformed YAML: {exc}") from None
    try:
        return parse_taxonomy(raw)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from None


def judges_from_taxonomy(
    tax: Taxonomy, factories: Mapping[str, Callable[[KPI], Judge]]
) -> dict[str, Judge]:
    """One judge per KPI, keyed by KPI name (the spec says which node it reads)."""
    judges: dict[str, Judge] = {}
    for spec in tax.kpi_specs:
        if spec.judge == "code":
            judges[spec.name] = code_judge(spec.to_kpi(), spec.check or {})
            continue
        if spec.judge not in factories:
            raise ValueError(f"KPI '{spec.name}': no factory registered for judge '{spec.judge}'")
        judges[spec.name] = factories[spec.judge](spec.to_kpi())
    return judges
