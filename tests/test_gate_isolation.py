"""T154: a PR can't change what grades it. With --policy-dir, `lgcov ci` reads lgcov.yaml, the
taxonomy and the bands from the base commit, and a PR's edits to them wait for its merge."""

import json
from pathlib import Path

import pytest

from lgcov.ci_report import CiResult, render_ci_report
from lgcov.cli import BOOTSTRAP_HEADLINE, main

FIXTURES = Path(__file__).parent / "fixtures"
DEMO_GRAPH = (FIXTURES / "demo_graph.py").read_text(encoding="utf-8")
BASELINE = (FIXTURES / "demo_baseline.json").read_text(encoding="utf-8")
DRIFTED_GRAPH = DEMO_GRAPH.replace("a concise", "a friendly")
CONFIG = "graph_entrypoint: graph.py:graph\ncassette_dir: cassettes\n"
KPI = "answer_states_order_status"
FAILS_SOME = 'check: {fail_every: "2", when_input_contains: "order"}'
# n=2 can't decide this KPI: INCONCLUSIVE. Blocking, that exits 1 under --fail-on-inconclusive;
# advisory, it exits 0. So the exit code shows whose policy the gate read.
TAXONOMY = f"""\
version: 1
system: demo_agent
failure_modes:
  - id: respond_drift
    description: The answer loses the order status.
kpis:
  - name: {KPI}
    failure_mode: respond_drift
    node: respond
    judge: fake
    description: The 2nd order answer judged fails; with k=1 that is the PR's.
    rubric: Does the answer state the order's status?
    {FAILS_SOME}
"""


def _bands(band: str) -> str:
    return json.dumps({"version": 1, "jev_model": None, "judges": {KPI: {"band": band}}})


CHECKOUT = {
    "graph.py": DEMO_GRAPH,
    "cassettes/baseline.json": BASELINE,
    "lgcov.yaml": CONFIG,
    "taxonomy.yaml": TAXONOMY,
    "calibration/bands.json": _bands("blocking"),
}


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.fixture
def pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The PR's checkout, as the working directory. It matches ../base until a test edits it."""
    _write(tmp_path / "base", CHECKOUT)
    _write(tmp_path / "pr", CHECKOUT)
    monkeypatch.chdir(tmp_path / "pr")
    return tmp_path / "pr"


def _ci(*extra: str, policy: bool = True, taxonomy: bool = True) -> tuple[int, str, dict[str, str]]:
    """`lgcov ci` as the Action runs it on a PR; policy=False: as it did before T154."""
    report, outputs = Path("../out/report.md"), Path("../out/outputs.txt")
    report.parent.mkdir(exist_ok=True)
    outputs.write_text("", encoding="utf-8")
    args = ["ci", "--report", str(report), "--github-output", str(outputs)]
    args += ["--base-dir", "../base", "--fail-on-inconclusive"]
    args += ["--policy-dir", "../base"] if policy else []
    args += ["--taxonomy", "taxonomy.yaml"] if taxonomy else []
    code = main([*args, *extra])
    lines = outputs.read_text(encoding="utf-8").splitlines()
    return (
        code,
        report.read_text(encoding="utf-8"),
        {k: v for k, v in (x.split("=", 1) for x in lines)},
    )


ATTACKS = {
    "taxonomy drops the KPI": {"taxonomy.yaml": TAXONOMY.replace(KPI, "pr_kpi")},
    "bands make it advisory": {"calibration/bands.json": _bands("advisory")},
    "advisory_kpis names it": {"lgcov.yaml": CONFIG + f"gate: {{advisory_kpis: [{KPI}]}}\n"},
    "blocking_kpis leaves it out": {"lgcov.yaml": CONFIG + "gate: {blocking_kpis: []}\n"},
}


@pytest.mark.parametrize("edits", ATTACKS.values(), ids=ATTACKS.keys())
def test_a_pr_edit_to_the_policy_no_longer_gets_it_past_the_gate(
    pr: Path, edits: dict[str, str]
) -> None:
    _write(pr, edits)
    assert _ci(policy=False)[0] == 0  # the attack works when the gate reads the PR's policy
    code, report, outputs = _ci()
    assert (code, outputs["verdict"]) == (1, "fail")
    assert f"| {KPI} | 2 |" in report and "pr_kpi" not in report


@pytest.mark.parametrize(
    "edits",
    [
        {"lgcov.yaml": CONFIG + "gate: {margin: 0.9}\n"},
        {"taxonomy.yaml": TAXONOMY + "    margin: 0.9\n"},
    ],
    ids=["gate.margin", "the KPI's own margin"],
)
def test_a_pr_edit_to_a_margin_leaves_the_base_margin(pr: Path, edits: dict[str, str]) -> None:
    _write(pr, edits)
    assert "| 0.90 |" in _ci(policy=False)[1]
    report = _ci()[1]
    assert "| 0.05 |" in report and "| 0.90 |" not in report


def test_a_pr_edit_to_ignore_paths_does_not_mask_its_drift(pr: Path) -> None:
    _write(pr, {"graph.py": DRIFTED_GRAPH, "lgcov.yaml": CONFIG + "ignore_paths: [/answer]\n"})
    assert _ci(policy=False, taxonomy=False)[0] == 0
    code, report, _ = _ci(taxonomy=False)
    assert code == 1
    assert "| `respond` | root | 2/2 |" in report


def test_an_absolute_path_into_the_pr_checkout_still_reads_the_base(pr: Path) -> None:
    _write(pr, {"calibration/bands.json": _bands("advisory")})
    code, _, _ = _ci("--bands", str(pr / "calibration" / "bands.json"))
    assert code == 1


def test_the_report_names_the_policy_edits_that_wait_for_the_merge(pr: Path) -> None:
    assert "after it merges" not in _ci()[1]
    _write(pr, ATTACKS["bands make it advisory"] | {"lgcov.yaml": CONFIG + "tier: pr\n"})
    report = _ci()[1]
    assert "`lgcov.yaml`, `calibration/bands.json`" in report
    assert "read from the base commit, so this PR's edits apply after it merges" in report


def _results(monkeypatch: pytest.MonkeyPatch) -> list[CiResult]:
    seen: list[CiResult] = []

    def render(result: CiResult) -> str:
        seen.append(result)
        return render_ci_report(result)

    monkeypatch.setattr("lgcov.cli.render_ci_report", render)
    return seen


def test_cassettes_a_pr_edits_are_honoured_and_flagged(
    pr: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _results(monkeypatch)
    _ci(taxonomy=False)
    assert not seen[-1].cassettes_edited
    # T153's rule reads this flag: until then, re-baselining in the PR still turns the gate green.
    _write(pr, {"graph.py": DRIFTED_GRAPH})
    assert main(["cassette", "accept", "--all-drifted"]) == 0
    code, report, _ = _ci(taxonomy=False)
    assert code == 0
    assert seen[-1].cassettes_edited
    assert "⚠️ This PR changes `cassettes`, the expected outputs" in report


def test_the_first_adoption_pr_is_advisory_with_the_pr_s_own_policy(
    pr: Path, tmp_path: Path
) -> None:
    for name in CHECKOUT:
        (tmp_path / "base" / name).unlink()
    _write(pr, {"graph.py": DRIFTED_GRAPH, "calibration/bands.json": _bands("advisory")})
    code, report, outputs = _ci()
    assert (code, outputs["verdict"], outputs["exit-code"]) == (0, "advisory", "0")
    assert report.splitlines()[1] == f"## ⚠️ {BOOTSTRAP_HEADLINE}"
    assert f"| {KPI} (advisory) | 2 |" in report  # the PR's bands: there is no other policy
    assert "| `respond` | root | 2/2 |" in report  # what it found is still shown
    assert "verdict: **advisory**" in report and "**pass**" not in report


def test_the_first_adoption_pr_never_claims_ship(pr: Path, tmp_path: Path) -> None:
    for name in CHECKOUT:
        (tmp_path / "base" / name).unlink()
    runs = json.loads(BASELINE)["runs"] * 40  # identical sides SHIP from 74 cases (T150)
    _write(
        pr,
        {
            "cassettes/baseline.json": json.dumps({"runs": runs}),
            "taxonomy.yaml": TAXONOMY.replace(FAILS_SOME, 'check: {fail_every: "0"}'),
        },
    )
    code, report, outputs = _ci()
    assert (code, outputs["verdict"]) == (0, "advisory")
    assert "would SHIP" in report
    assert "🟢" not in report
    assert "What SHIP and BLOCK guarantee" not in report


def test_a_pr_cannot_reach_bootstrap_once_the_base_has_config(pr: Path) -> None:
    (pr / "lgcov.yaml").unlink()
    _write(pr, {"graph.py": DRIFTED_GRAPH})
    code, report, outputs = _ci(taxonomy=False)
    assert (code, outputs["verdict"]) == (1, "fail")
    assert BOOTSTRAP_HEADLINE not in report


def test_a_base_gating_through_action_inputs_is_not_bootstrap(pr: Path, tmp_path: Path) -> None:
    # No lgcov.yaml on the base, but it names a graph and has a baseline: its gate is live, so
    # a PR that adds lgcov.yaml can't turn it advisory.
    (tmp_path / "base" / "lgcov.yaml").unlink()
    _write(pr, {"graph.py": DRIFTED_GRAPH, "lgcov.yaml": CONFIG + "ignore_paths: [/answer]\n"})
    code, report, _ = _ci("--graph", "graph.py:graph", taxonomy=False)
    assert code == 1
    assert BOOTSTRAP_HEADLINE not in report


def test_a_real_harness_error_in_bootstrap_still_exits_two(pr: Path, tmp_path: Path) -> None:
    for name in CHECKOUT:
        (tmp_path / "base" / name).unlink()
    _write(pr, {"graph.py": 'raise RuntimeError("boom at import")\n'})
    code, report, outputs = _ci(taxonomy=False)
    assert (code, outputs["verdict"]) == (2, "error")
    assert "boom at import" in report
    assert BOOTSTRAP_HEADLINE not in report


def test_moving_the_graph_while_the_base_has_config_exits_two_and_says_why(pr: Path) -> None:
    (pr / "graph.py").rename(pr / "agent.py")
    _write(pr, {"lgcov.yaml": "graph_entrypoint: agent.py:graph\ncassette_dir: cassettes\n"})
    code, report, outputs = _ci(taxonomy=False)
    assert (code, outputs["verdict"]) == (2, "error")
    assert "lgcov.yaml on the base names `graph.py:graph`" in report
    assert BOOTSTRAP_HEADLINE not in report


def test_a_taxonomy_only_this_pr_has_exits_two_and_says_when_it_applies(
    pr: Path, tmp_path: Path
) -> None:
    (tmp_path / "base" / "taxonomy.yaml").unlink()
    code, report, outputs = _ci()
    assert (code, outputs["verdict"]) == (2, "error")
    assert "read from the base commit, so this PR's edits apply after it merges" in report


def test_a_broken_lgcov_yaml_on_the_base_says_a_fix_applies_after_it_merges(
    pr: Path, tmp_path: Path
) -> None:
    _write(tmp_path / "base", {"lgcov.yaml": "tier: every-week\n" + CONFIG})
    code, report, _ = _ci()
    assert code == 2
    assert "read from the base commit, so this PR's edits apply after it merges" in report
