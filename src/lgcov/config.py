"""T080: lgcov.yaml -> a typed Config. Precedence: CLI flag > file > default."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_PATH = "lgcov.yaml"
FAIL_ON_VALUES = ("fail", "infra_error", "needs_human")  # judge_gate.GateOutcome minus "pass"


@dataclass(frozen=True)
class Thresholds:
    min_node_coverage: float = 0.0  # percent, 0..100
    max_drifted_nodes: int | None = None  # None = no limit
    fail_on: tuple[str, ...] = ("fail",)


@dataclass(frozen=True)
class Config:
    graph_entrypoint: str  # "<module or file.py>:<attr>"
    cassette_dir: str = "cassettes"
    taxonomy_path: str | None = None
    thresholds: Thresholds = field(default_factory=Thresholds)
    ignore_paths: tuple[str, ...] = ()  # JSON pointers into node I/O, e.g. /meta/nonce
    samples_per_case: int = 1  # T073: k judge verdicts per case (pass@k / pass^k)


_KEYS = (
    "cassette_dir",
    "graph_entrypoint",
    "ignore_paths",
    "samples_per_case",
    "taxonomy",  # alias for taxonomy_path
    "taxonomy_path",
    "thresholds",
)
_THRESHOLD_KEYS = ("fail_on", "max_drifted_nodes", "min_node_coverage")


def _check_keys(d: Mapping[str, object], valid: tuple[str, ...], where: str) -> None:
    unknown = sorted(set(d) - set(valid))
    if unknown:
        raise ValueError(f"{where}: unknown keys {unknown}, valid keys: {list(valid)}")


def _str(value: object, key: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"lgcov.yaml: '{key}' must be a non-empty string")
    return value


def _str_list(value: object, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"lgcov.yaml: '{key}' must be a list of strings")
    return tuple(value)


def _positive_int(value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"lgcov.yaml: '{key}' must be a positive integer")
    return value


def _nonneg_int(value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"lgcov.yaml: '{key}' must be a non-negative integer")
    return value


def _thresholds(raw: object) -> Thresholds:
    if not isinstance(raw, dict):
        raise ValueError("lgcov.yaml: 'thresholds' must be a mapping")
    _check_keys(raw, _THRESHOLD_KEYS, "lgcov.yaml: thresholds")
    cov = raw.get("min_node_coverage", 0.0)
    if isinstance(cov, bool) or not isinstance(cov, int | float) or not 0 <= cov <= 100:
        raise ValueError("lgcov.yaml: 'min_node_coverage' must be a number in 0..100")
    fail_on = _str_list(raw.get("fail_on", ["fail"]), "fail_on")
    bad = sorted(set(fail_on) - set(FAIL_ON_VALUES))
    if bad:
        raise ValueError(f"lgcov.yaml: 'fail_on' values {bad} not in {list(FAIL_ON_VALUES)}")
    max_drifted = raw.get("max_drifted_nodes")
    capped = None if max_drifted is None else _nonneg_int(max_drifted, "max_drifted_nodes")
    return Thresholds(
        min_node_coverage=float(cov),
        max_drifted_nodes=capped,
        fail_on=fail_on,
    )


def _taxonomy_path(d: Mapping[str, object]) -> str | None:
    path = d.get("taxonomy_path")
    alias = d.get("taxonomy")
    if path is not None and alias is not None and path != alias:
        raise ValueError(
            "lgcov.yaml: 'taxonomy_path' and 'taxonomy' both set and disagree; "
            "use taxonomy_path only"
        )
    chosen = path if path is not None else alias
    return None if chosen is None else _str(chosen, "taxonomy_path")


def parse_config(raw: object, overrides: Mapping[str, str | None] | None = None) -> Config:
    """Validate a parsed lgcov.yaml (pure). Overrides whose value is None are ignored."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or not all(isinstance(k, str) for k in raw):
        raise ValueError("lgcov.yaml: top level must be a mapping")
    _check_keys(raw, _KEYS, "lgcov.yaml")
    d = dict(raw) | {k: v for k, v in (overrides or {}).items() if v is not None}
    if "graph_entrypoint" not in d:
        raise ValueError("lgcov.yaml: 'graph_entrypoint' is required ('<file.py>:<attr>')")
    entry = _str(d["graph_entrypoint"], "graph_entrypoint")
    target, _, attr = entry.rpartition(":")
    if not target or not attr:
        raise ValueError(
            f"lgcov.yaml: 'graph_entrypoint' must be '<file.py>:<attr>', got {entry!r}"
        )
    ignore = _str_list(d.get("ignore_paths", []), "ignore_paths")
    if not all(p.startswith("/") for p in ignore):
        raise ValueError("lgcov.yaml: 'ignore_paths' must be JSON pointers starting with '/'")
    return Config(
        graph_entrypoint=entry,
        cassette_dir=_str(d.get("cassette_dir", "cassettes"), "cassette_dir"),
        taxonomy_path=_taxonomy_path(d),
        thresholds=_thresholds(d.get("thresholds", {})),
        ignore_paths=ignore,
        samples_per_case=_positive_int(d.get("samples_per_case", 1), "samples_per_case"),
    )


def load_config(
    path: Path | str = DEFAULT_PATH, overrides: Mapping[str, str | None] | None = None
) -> Config:
    """Read lgcov.yaml; a missing file means all defaults (graph_entrypoint still required)."""
    path = Path(path)
    raw: object = None
    if path.is_file():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"{path}: malformed YAML: {exc}") from None
    return parse_config(raw, overrides)
