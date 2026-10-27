"""T080: lgcov.yaml -> typed Config; CLI overrides win; missing file = defaults."""

from pathlib import Path

import pytest

from lgcov.config import Config, Thresholds, load_config, parse_config

REPO_CONFIG = Path(__file__).resolve().parents[1] / "lgcov.yaml"


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "lgcov.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_full_file_loads_typed(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "graph_entrypoint: app/graph.py:graph\n"
        "cassette_dir: cassettes\n"
        "taxonomy: taxonomy.yaml\n"
        "thresholds: {min_node_coverage: 80, fail_on: [fail, infra_error]}\n"
        "ignore_paths: [/steps/0/output/id]\n",
    )
    assert load_config(path) == Config(
        graph_entrypoint="app/graph.py:graph",
        cassette_dir="cassettes",
        taxonomy="taxonomy.yaml",
        thresholds=Thresholds(min_node_coverage=80.0, fail_on=("fail", "infra_error")),
        ignore_paths=("/steps/0/output/id",),
    )


def test_missing_graph_entrypoint_names_the_field(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="graph_entrypoint"):
        load_config(_write(tmp_path, "cassette_dir: cassettes\n"))


def test_cli_override_beats_file_value(tmp_path: Path) -> None:
    path = _write(tmp_path, "graph_entrypoint: a.py:graph\ncassette_dir: from_file\n")
    cfg = load_config(path, {"cassette_dir": "from_flag", "graph_entrypoint": None})
    assert cfg.cassette_dir == "from_flag"
    assert cfg.graph_entrypoint == "a.py:graph"  # None means "flag not given"


def test_unknown_key_lists_valid_keys(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError, match=r"graph_entry.*valid keys.*cassette_dir.*graph_entrypoint"
    ):
        load_config(_write(tmp_path, "graph_entry: a.py:graph\n"))


def test_unknown_threshold_key_lists_valid_keys() -> None:
    raw = {"graph_entrypoint": "a.py:g", "thresholds": {"min_cov": 1}}
    with pytest.raises(ValueError, match=r"min_cov.*fail_on.*min_node_coverage"):
        parse_config(raw)


def test_missing_file_uses_defaults_but_still_needs_entrypoint(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ValueError, match="graph_entrypoint"):
        load_config(missing)
    cfg = load_config(missing, {"graph_entrypoint": "a.py:graph"})
    assert cfg == Config(graph_entrypoint="a.py:graph")
    assert cfg.thresholds == Thresholds(min_node_coverage=0.0, fail_on=("fail",))


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        ({"graph_entrypoint": "no_colon"}, "graph_entrypoint"),
        ({"graph_entrypoint": "a.py:g", "thresholds": {"fail_on": ["oops"]}}, "fail_on"),
        ({"graph_entrypoint": "a.py:g", "thresholds": {"min_node_coverage": 101}}, "min_node"),
        ({"graph_entrypoint": "a.py:g", "ignore_paths": ["no/leading/slash"]}, "ignore_paths"),
        ({"graph_entrypoint": "a.py:g", "cassette_dir": 3}, "cassette_dir"),
    ],
)
def test_bad_values_name_the_field(raw: dict[str, object], field: str) -> None:
    with pytest.raises(ValueError, match=field):
        parse_config(raw)


def test_repo_lgcov_yaml_is_valid() -> None:
    cfg = load_config(REPO_CONFIG)
    assert cfg.graph_entrypoint == "examples/demo_agent/graph.py:graph"
    assert (REPO_CONFIG.parent / cfg.cassette_dir / "baseline.json").is_file()
