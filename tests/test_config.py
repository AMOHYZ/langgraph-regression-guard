"""T080: lgcov.yaml -> typed Config; CLI overrides win; missing file = defaults."""

from pathlib import Path

import pytest

from lgcov.config import Config, GateConfig, Thresholds, load_config, parse_config

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
        "taxonomy_path: taxonomy.yaml\n"
        "thresholds: {min_node_coverage: 80, max_drifted_nodes: 3, fail_on: [fail, infra_error]}\n"
        "ignore_paths: [/meta/nonce]\n",
    )
    assert load_config(path) == Config(
        graph_entrypoint="app/graph.py:graph",
        cassette_dir="cassettes",
        taxonomy_path="taxonomy.yaml",
        thresholds=Thresholds(
            min_node_coverage=80.0, max_drifted_nodes=3, fail_on=("fail", "infra_error")
        ),
        ignore_paths=("/meta/nonce",),
    )


def test_taxonomy_alias_maps_to_taxonomy_path(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, "graph_entrypoint: a.py:g\ntaxonomy: old.yaml\n"))
    assert cfg.taxonomy_path == "old.yaml"


def test_both_taxonomy_keys_must_agree(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="taxonomy_path"):
        load_config(
            _write(
                tmp_path,
                "graph_entrypoint: a.py:g\ntaxonomy: a.yaml\ntaxonomy_path: b.yaml\n",
            )
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
    with pytest.raises(ValueError, match=r"min_cov.*fail_on.*max_drifted_nodes.*min_node_coverage"):
        parse_config(raw)


def test_missing_file_uses_defaults_but_still_needs_entrypoint(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ValueError, match="graph_entrypoint"):
        load_config(missing)
    cfg = load_config(missing, {"graph_entrypoint": "a.py:graph"})
    assert cfg == Config(graph_entrypoint="a.py:graph")
    assert cfg.thresholds == Thresholds(
        min_node_coverage=0.0, max_drifted_nodes=None, fail_on=("fail",)
    )


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        ({"graph_entrypoint": "no_colon"}, "graph_entrypoint"),
        ({"graph_entrypoint": "a.py:g", "thresholds": {"fail_on": ["oops"]}}, "fail_on"),
        ({"graph_entrypoint": "a.py:g", "thresholds": {"min_node_coverage": 101}}, "min_node"),
        ({"graph_entrypoint": "a.py:g", "thresholds": {"max_drifted_nodes": -1}}, "max_drifted"),
        ({"graph_entrypoint": "a.py:g", "ignore_paths": ["no/leading/slash"]}, "ignore_paths"),
        ({"graph_entrypoint": "a.py:g", "cassette_dir": 3}, "cassette_dir"),
        ({"graph_entrypoint": "a.py:g", "samples_per_case": 0}, "samples_per_case"),
        ({"graph_entrypoint": "a.py:g", "samples_per_case": "3"}, "samples_per_case"),
        ({"graph_entrypoint": "a.py:g", "samples_per_case": True}, "samples_per_case"),
        ({"graph_entrypoint": "a.py:g", "gate": {"alpha": 0}}, "alpha"),
        ({"graph_entrypoint": "a.py:g", "gate": {"power": 1}}, "power"),
        ({"graph_entrypoint": "a.py:g", "gate": {"margin": -0.1}}, "margin"),
        ({"graph_entrypoint": "a.py:g", "gate": {"inconclusive_blocks": "yes"}}, "inconclusive"),
        ({"graph_entrypoint": "a.py:g", "gate": {"advisory_kpis": "tone"}}, "advisory_kpis"),
        ({"graph_entrypoint": "a.py:g", "gate": {"holm": True}}, "valid keys"),
        (
            {
                "graph_entrypoint": "a.py:g",
                "gate": {"blocking_kpis": ["a"], "advisory_kpis": ["a"]},
            },
            "both blocking and advisory",
        ),
    ],
)
def test_bad_values_name_the_field(raw: dict[str, object], field: str) -> None:
    with pytest.raises(ValueError, match=field):
        parse_config(raw)


def test_repo_lgcov_yaml_is_valid() -> None:
    cfg = load_config(REPO_CONFIG)
    assert cfg.graph_entrypoint == "examples/demo_agent/graph.py:graph"
    assert (REPO_CONFIG.parent / cfg.cassette_dir / "baseline.json").is_file()


def test_gate_defaults() -> None:
    # T091: alpha 0.05, power 0.8, margin 0.05; every KPI blocks; INCONCLUSIVE exits 0.
    gate = parse_config({"graph_entrypoint": "a.py:g"}).gate
    assert gate == GateConfig(0.05, 0.8, 0.05, None, (), False)


def test_gate_section_loads_typed(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "graph_entrypoint: a.py:g\n"
        "gate:\n  alpha: 0.01\n  power: 0.9\n  margin: 0.02\n"
        "  blocking_kpis: [grounded]\n  advisory_kpis: [tone]\n  inconclusive_blocks: true\n",
    )
    assert load_config(path).gate == GateConfig(0.01, 0.9, 0.02, ("grounded",), ("tone",), True)


def test_gate_advisory_kpis() -> None:
    kpis = ["grounded", "tone", "polite"]
    assert GateConfig().advisory(kpis) == frozenset()
    assert GateConfig(advisory_kpis=("tone",)).advisory(kpis) == {"tone"}
    assert GateConfig(blocking_kpis=("grounded",)).advisory(kpis) == {"tone", "polite"}
    with pytest.raises(ValueError, match=r"\['typo'\]"):
        GateConfig(advisory_kpis=("typo",)).advisory(kpis)


def test_samples_per_case_defaults_to_one_and_reads_from_the_file() -> None:
    # T073: k binary verdicts per judged case.
    assert parse_config({"graph_entrypoint": "a.py:g"}).samples_per_case == 1
    assert parse_config({"graph_entrypoint": "a.py:g", "samples_per_case": 3}).samples_per_case == 3
