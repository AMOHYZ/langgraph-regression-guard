"""T075: taxonomy.yaml -> KPI objects -> one judge per KPI."""

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from lgcov.code_judges import ToolNotCalled
from lgcov.judges import KPI, FakeJudge, Judge
from lgcov.taxonomy import judges_from_taxonomy, load_taxonomy

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "open_deep_research" / "taxonomy.yaml"


def _fake(kpi: KPI) -> Judge:
    return FakeJudge(kpi, "pass")


FAKES: Mapping[str, Callable[[KPI], Judge]] = {"fake": _fake, "jev": _fake}


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "taxonomy.yaml"
    path.write_text(body, encoding="utf-8")
    return path


_ONE_MODE = "  - id: mode_a\n    description: first failure mode\n"


def _doc(kpis: str, *, version: str = "1", failure_modes: str = _ONE_MODE) -> str:
    return f"version: {version}\nsystem: sandbox\nfailure_modes:\n{failure_modes}kpis:\n{kpis}"


TWO_KPIS = _doc(
    """\
  - name: kpi_a
    failure_mode: mode_a
    node: supervisor
    judge: fake
    description: one line
    rubric: Did the supervisor delegate?
  - name: kpi_b
    failure_mode: mode_a
    node: final
    judge: fake
    description: one line
    rubric: Is the report grounded?
"""
)


def test_two_kpi_taxonomy_builds_two_judges(tmp_path: Path) -> None:
    tax = load_taxonomy(_write(tmp_path, TWO_KPIS))
    judges = judges_from_taxonomy(tax, FAKES)
    assert sorted(judges) == ["kpi_a", "kpi_b"]
    assert all(isinstance(j, FakeJudge) for j in judges.values())


def test_labels_and_score_map_default_to_binary(tmp_path: Path) -> None:
    doc = _doc(
        """\
  - name: kpi_a
    failure_mode: mode_a
    node: final
    judge: fake
    description: one line
    rubric: Is the report grounded?
"""
    )
    kpi = load_taxonomy(_write(tmp_path, doc)).kpis()[0]
    assert kpi.labels == ("pass", "fail")
    assert kpi.score_map == {"pass": 1.0, "fail": 0.0}


def test_unknown_failure_mode_names_the_kpi(tmp_path: Path) -> None:
    doc = _doc(
        """\
  - name: kpi_a
    failure_mode: ghost
    node: final
    judge: fake
    description: one line
    rubric: Is the report grounded?
"""
    )
    with pytest.raises(ValueError, match="kpi_a"):
        load_taxonomy(_write(tmp_path, doc))


def test_duplicate_kpi_name_rejected(tmp_path: Path) -> None:
    doc = _doc(
        """\
  - name: kpi_a
    failure_mode: mode_a
    node: final
    judge: fake
    description: one line
    rubric: Is the report grounded?
  - name: kpi_a
    failure_mode: mode_a
    node: supervisor
    judge: fake
    description: one line
    rubric: Did the supervisor delegate?
"""
    )
    with pytest.raises(ValueError, match="kpi_a"):
        load_taxonomy(_write(tmp_path, doc))


def test_unknown_judge_type_rejected(tmp_path: Path) -> None:
    doc = _doc(
        """\
  - name: kpi_a
    failure_mode: mode_a
    node: final
    judge: oracle
    description: one line
    rubric: Is the report grounded?
"""
    )
    with pytest.raises(ValueError, match="oracle"):
        load_taxonomy(_write(tmp_path, doc))


def test_code_judge_builds_tool_not_called(tmp_path: Path) -> None:
    doc = _doc(
        """\
  - name: kpi_a
    failure_mode: mode_a
    node: supervisor
    judge: code
    description: one line
    rubric: Did the supervisor avoid web search?
    check: {type: tool_not_called, tool: web_search}
"""
    )
    judges = judges_from_taxonomy(load_taxonomy(_write(tmp_path, doc)), FAKES)
    assert judges["kpi_a"] == ToolNotCalled(judges["kpi_a"].kpi, "web_search")


def test_code_judge_unknown_check_fails_at_load(tmp_path: Path) -> None:
    doc = _doc(
        """\
  - name: kpi_a
    failure_mode: mode_a
    node: supervisor
    judge: code
    description: one line
    rubric: Did the supervisor delegate?
    check: {type: oracle}
"""
    )
    with pytest.raises(ValueError, match=r"kpi_a.*oracle.*tool_not_called"):
        load_taxonomy(_write(tmp_path, doc))


def test_wrong_version_rejected(tmp_path: Path) -> None:
    doc = _doc(
        """\
  - name: kpi_a
    failure_mode: mode_a
    node: final
    judge: fake
    description: one line
    rubric: Is the report grounded?
""",
        version="2",
    )
    with pytest.raises(ValueError, match="version"):
        load_taxonomy(_write(tmp_path, doc))


def test_malformed_yaml_and_non_mapping_name_the_file(tmp_path: Path) -> None:
    path = _write(tmp_path, "version: [unclosed")
    with pytest.raises(ValueError) as excinfo:
        load_taxonomy(path)
    assert str(path) in str(excinfo.value)
    assert "malformed YAML" in str(excinfo.value)
    with pytest.raises(ValueError, match="mapping"):
        load_taxonomy(_write(tmp_path, "just a string"))


def test_label_hints_outside_labels_names_the_kpi(tmp_path: Path) -> None:
    doc = _doc(
        """\
  - name: kpi_a
    failure_mode: mode_a
    node: final
    judge: fake
    description: one line
    rubric: Is the report grounded?
    label_hints: {pass: fine, wrong: bogus}
"""
    )
    with pytest.raises(ValueError, match=r"kpi_a.*label_hints"):
        load_taxonomy(_write(tmp_path, doc))


def test_custom_labels_need_score_map(tmp_path: Path) -> None:
    doc = _doc(
        """\
  - name: kpi_a
    failure_mode: mode_a
    node: final
    judge: fake
    description: one line
    rubric: Is the report grounded?
    labels: [good, bad]
"""
    )
    with pytest.raises(ValueError, match=r"kpi_a.*score_map"):
        load_taxonomy(_write(tmp_path, doc))


def test_example_open_deep_research_builds_two_judges() -> None:
    judges = judges_from_taxonomy(load_taxonomy(EXAMPLE), FAKES)
    assert set(judges) == {"delegates_research", "report_grounded"}
    assert all(isinstance(j, FakeJudge) for j in judges.values())
