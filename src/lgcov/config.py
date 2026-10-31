"""T080: lgcov.yaml -> a typed Config. Precedence: CLI flag > file > default."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_PATH = "lgcov.yaml"


@dataclass(frozen=True)
class Thresholds:
    min_node_coverage: float = 0.0  # percent, 0..100; under it `lgcov ci` exits 1 (0 = off)


@dataclass(frozen=True)
class GateConfig:
    """T091: the L2 gate's paired test. Every KPI blocks unless it is advisory."""

    alpha: float = 0.05
    power: float = 0.8
    margin: float = 0.05  # non-inferiority margin δ; a KPI's own margin in taxonomy.yaml wins
    blocking_kpis: tuple[str, ...] | None = None  # None: every KPI not in advisory_kpis
    advisory_kpis: tuple[str, ...] = ()
    inconclusive_blocks: bool = False  # True: an INCONCLUSIVE blocking KPI exits 1, not 0

    def advisory(self, kpis: Collection[str]) -> frozenset[str]:
        """The advisory KPIs among a taxonomy's. A name the taxonomy doesn't have is an error."""
        unknown = sorted((set(self.advisory_kpis) | set(self.blocking_kpis or ())) - set(kpis))
        if unknown:
            raise ValueError(
                f"lgcov.yaml: gate names KPIs {unknown} that the taxonomy doesn't have "
                f"(it has {sorted(kpis)})"
            )
        if self.blocking_kpis is None:
            return frozenset(self.advisory_kpis)
        return frozenset(k for k in kpis if k not in self.blocking_kpis)


@dataclass(frozen=True)
class Config:
    graph_entrypoint: str  # "<module or file.py>:<attr>"
    cassette_dir: str = "cassettes"
    taxonomy_path: str | None = None
    thresholds: Thresholds = field(default_factory=Thresholds)
    ignore_paths: tuple[str, ...] = ()  # JSON pointers into node I/O, e.g. /meta/nonce
    samples_per_case: int = 1  # T073: k judge verdicts per case (pass@k / pass^k)
    gate: GateConfig = field(default_factory=GateConfig)


_KEYS = (
    "cassette_dir",
    "gate",
    "graph_entrypoint",
    "ignore_paths",
    "samples_per_case",
    "taxonomy",  # alias for taxonomy_path
    "taxonomy_path",
    "thresholds",
)
_THRESHOLD_KEYS = ("min_node_coverage",)
# T080 keys no command ever read; a file that still sets one is told what does that job.
_REMOVED_THRESHOLD_KEYS = {
    "fail_on": "a blocking KPI's BLOCK always fails; gate.inconclusive_blocks fails INCONCLUSIVE",
    "max_drifted_nodes": "--fail-on (root-drift, any-drift or never) picks the drift that fails",
}
_GATE_KEYS = (
    "advisory_kpis",
    "alpha",
    "blocking_kpis",
    "inconclusive_blocks",
    "margin",
    "power",
)


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


def _thresholds(raw: object) -> Thresholds:
    if not isinstance(raw, dict):
        raise ValueError("lgcov.yaml: 'thresholds' must be a mapping")
    for key, instead in _REMOVED_THRESHOLD_KEYS.items():
        if key in raw:
            raise ValueError(
                f"lgcov.yaml: 'thresholds.{key}' was removed, as no command read it: {instead}"
            )
    _check_keys(raw, _THRESHOLD_KEYS, "lgcov.yaml: thresholds")
    cov = raw.get("min_node_coverage", 0.0)
    if isinstance(cov, bool) or not isinstance(cov, int | float) or not 0 <= cov <= 100:
        raise ValueError("lgcov.yaml: 'min_node_coverage' must be a number in 0..100")
    return Thresholds(min_node_coverage=float(cov))


def _probability(value: object, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value < 1:
        raise ValueError(f"lgcov.yaml: 'gate.{key}' must be a number between 0 and 1")
    return float(value)


def _gate(raw: object) -> GateConfig:
    if not isinstance(raw, dict):
        raise ValueError("lgcov.yaml: 'gate' must be a mapping")
    _check_keys(raw, _GATE_KEYS, "lgcov.yaml: gate")
    margin = raw.get("margin", 0.05)
    if isinstance(margin, bool) or not isinstance(margin, int | float) or not 0 <= margin <= 1:
        raise ValueError("lgcov.yaml: 'gate.margin' must be a number in 0..1")
    blocking = raw.get("blocking_kpis")
    blocking_kpis = None if blocking is None else _str_list(blocking, "gate.blocking_kpis")
    advisory_kpis = _str_list(raw.get("advisory_kpis", []), "gate.advisory_kpis")
    both = sorted(set(blocking_kpis or ()) & set(advisory_kpis))
    if both:
        raise ValueError(f"lgcov.yaml: gate KPIs {both} are both blocking and advisory")
    inconclusive_blocks = raw.get("inconclusive_blocks", False)
    if not isinstance(inconclusive_blocks, bool):
        raise ValueError("lgcov.yaml: 'gate.inconclusive_blocks' must be true or false")
    return GateConfig(
        alpha=_probability(raw.get("alpha", 0.05), "alpha"),
        power=_probability(raw.get("power", 0.8), "power"),
        margin=float(margin),
        blocking_kpis=blocking_kpis,
        advisory_kpis=advisory_kpis,
        inconclusive_blocks=inconclusive_blocks,
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
        gate=_gate(d.get("gate", {})),
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
