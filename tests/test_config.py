"""T080: lgcov.yaml -> typed Config; CLI overrides win; missing file = defaults."""

from pathlib import Path

import pytest

from lgcov.config import (
    Config,
    GateConfig,
    Thresholds,
    TierPolicy,
    load_config,
    parse_config,
    tier_policy,
)

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
        "thresholds: {min_node_coverage: 80}\n"
        "ignore_paths: [/meta/nonce]\n",
    )
    assert load_config(path) == Config(
        graph_entrypoint="app/graph.py:graph",
        cassette_dir="cassettes",
        taxonomy_path="taxonomy.yaml",
        thresholds=Thresholds(min_node_coverage=80.0),
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
    with pytest.raises(ValueError, match=r"min_cov.*valid keys: \['min_node_coverage'\]"):
        parse_config(raw)


def test_missing_file_uses_defaults_but_still_needs_entrypoint(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ValueError, match="graph_entrypoint"):
        load_config(missing)
    cfg = load_config(missing, {"graph_entrypoint": "a.py:graph"})
    assert cfg == Config(graph_entrypoint="a.py:graph")
    assert cfg.thresholds == Thresholds(min_node_coverage=0.0)


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        ({"graph_entrypoint": "no_colon"}, "graph_entrypoint"),
        ({"graph_entrypoint": "a.py:g", "thresholds": {"min_node_coverage": 101}}, "min_node"),
        ({"graph_entrypoint": "a.py:g", "ignore_paths": ["no/leading/slash"]}, "ignore_paths"),
        ({"graph_entrypoint": "a.py:g", "cassette_dir": 3}, "cassette_dir"),
        ({"graph_entrypoint": "a.py:g", "samples_per_case": 0}, "samples_per_case"),
        ({"graph_entrypoint": "a.py:g", "samples_per_case": "3"}, "samples_per_case"),
        ({"graph_entrypoint": "a.py:g", "samples_per_case": True}, "samples_per_case"),
        ({"graph_entrypoint": "a.py:g", "tier": "weekly"}, "tier"),
        ({"graph_entrypoint": "a.py:g", "gate": {"uncalibrated": "blocking"}}, "uncalibrated"),
        ({"graph_entrypoint": "a.py:g", "jev_model": ""}, "jev_model"),
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


# No command ever read T080's fail_on or max_drifted_nodes; a file that still sets one is
# told what does that job.
@pytest.mark.parametrize(
    ("key", "value", "instead"),
    [
        ("fail_on", ["fail", "needs_human"], "gate.inconclusive_blocks"),
        ("max_drifted_nodes", 3, "--fail-on"),
    ],
)
def test_removed_threshold_keys_say_what_does_their_job(
    key: str, value: object, instead: str
) -> None:
    raw = {"graph_entrypoint": "a.py:g", "thresholds": {"min_node_coverage": 80, key: value}}
    with pytest.raises(ValueError, match=f"thresholds.{key}.*{instead}"):
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


def test_samples_per_case_is_unset_by_default_and_reads_from_the_file() -> None:
    # T073: k binary verdicts per judged case. T089: unset, the tier picks k (1 pr, 3 nightly).
    assert parse_config({"graph_entrypoint": "a.py:g"}).samples_per_case is None
    assert parse_config({"graph_entrypoint": "a.py:g", "samples_per_case": 3}).samples_per_case == 3


def test_tier_defaults_to_pr_and_reads_from_the_file() -> None:
    assert parse_config({"graph_entrypoint": "a.py:g"}).tier == "pr"
    assert parse_config({"graph_entrypoint": "a.py:g", "tier": "nightly"}).tier == "nightly"


def test_tier_policy() -> None:
    # T089: pr serves unchanged nodes, judges once, skips general LLM judges, within 90 s;
    # nightly runs every node live with every judge, k from samples_per_case (3 if unset).
    assert tier_policy("pr", None) == TierPolicy(serve=True, k=1, budget=90.0, llm_judges=False)
    assert tier_policy("pr", 5).k == 1
    assert tier_policy("nightly", None) == TierPolicy(False, 3, None, True)
    assert tier_policy("nightly", 5).k == 5


def test_gate_uncalibrated_and_the_pinned_jev_model() -> None:
    # T063: what a judge with no labels may do, and the Jev model labels were made with.
    cfg = parse_config({"graph_entrypoint": "a.py:g"})
    assert (cfg.gate.uncalibrated, cfg.jev_model) == ("advisory", None)
    raw = {
        "graph_entrypoint": "a.py:g",
        "jev_model": "jev-2026-10",
        "gate": {"uncalibrated": "flag"},
    }
    cfg = parse_config(raw)
    assert (cfg.gate.uncalibrated, cfg.jev_model) == ("flag", "jev-2026-10")
