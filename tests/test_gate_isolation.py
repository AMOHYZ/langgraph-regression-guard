"""T154: a PR can't change what grades it. With --policy-dir, `lgcov ci` reads lgcov.yaml, the
taxonomy and the bands from the base commit, and a PR's edits to them wait for its merge."""

import json
from pathlib import Path

import pytest

from lgcov.ci_report import CiResult, render_ci_report
from lgcov.cli import main
from lgcov.verdict_actions import BOOTSTRAP_HEADLINE

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


def _ci(
    *extra: str, policy: bool = True, taxonomy: bool = True, switch: bool = True
) -> tuple[int, str, dict[str, str]]:
    """`lgcov ci` as the Action runs it on a PR; policy=False: as it did before T154."""
    report, outputs = Path("../out/report.md"), Path("../out/outputs.txt")
    report.parent.mkdir(exist_ok=True)
    outputs.write_text("", encoding="utf-8")
    args = ["ci", "--report", str(report), "--github-output", str(outputs)]
    args += ["--base-dir", "../base", *(["--fail-on-inconclusive"] if switch else [])]
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
    assert (code, outputs["verdict"]) == (1, "needs_human")
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
    # T153: a re-baseline can still land in its own PR, even with the switch on, but it can't
    # read as a pass.
    _write(pr, {"graph.py": DRIFTED_GRAPH})
    assert main(["cassette", "accept", "--all-drifted"]) == 0
    code, report, outputs = _ci(taxonomy=False)
    assert (code, outputs["verdict"]) == (0, "needs_human")
    assert report.splitlines()[1] == "## ⚠️ No decision: needs_human"
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


# A re-baseline moves what a PR's code checks are compared with, so they also run against the
# base's recorded runs. 4 cases at this margin: identical sides SHIP.
CODE_TAXONOMY = """\
version: 1
system: demo_agent
failure_modes:
  - id: skips_the_lookup
    description: An order question no longer reaches the order lookup.
kpis:
  - name: lookup_runs
    failure_mode: skips_the_lookup
    node: lookup_order
    judge: code
    margin: 0.95
    description: Order questions look the order up.
    rubric: Did the run visit lookup_order?
    check: {type: node_visited, node: lookup_order}
"""
CLASSIFY = 'return {"needs_tool": "order" in state["question"].lower()}'
SKIPS_THE_LOOKUP = DEMO_GRAPH.replace(CLASSIFY, 'return {"needs_tool": False}')
REWRITES_THE_QUESTION = DEMO_GRAPH.replace(
    CLASSIFY,
    'return {"question": state["question"].lower(), '
    '"needs_tool": "order" in state["question"].lower()}',
)
HIDDEN = "## 🔴 code check failed (hidden by a re-baseline in this PR)"
NO_DECISION = "## ⚠️ No decision: needs_human"


@pytest.fixture
def gated(pr: Path, tmp_path: Path) -> Path:
    """Both checkouts gate on the code check: run 0 and 2 pass it, and 1 and 3 never can."""
    files = {
        "taxonomy.yaml": CODE_TAXONOMY,
        "cassettes/baseline.json": json.dumps({"runs": json.loads(BASELINE)["runs"] * 2}),
        "calibration/bands.json": json.dumps({"version": 1, "jev_model": None, "judges": {}}),
    }
    _write(tmp_path / "base", files)
    _write(pr, files)
    return pr


def _re_record(reverse: bool = False) -> None:
    runs = json.loads(Path("cassettes/baseline.json").read_text(encoding="utf-8"))["runs"]
    inputs = [r["inputs"] for r in (runs[::-1] if reverse else runs)]
    Path("../inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
    assert main(["record", "--inputs", "../inputs.json"]) == 0


def _edit_cassettes_without_re_baselining() -> None:
    path = Path("cassettes/baseline.json")
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")), indent=1), "utf-8")


def test_a_re_baseline_cannot_hide_a_code_check_that_now_fails(gated: Path) -> None:
    _write(gated, {"graph.py": SKIPS_THE_LOOKUP})
    _re_record()
    code, report, outputs = _ci()
    assert (code, outputs["verdict"]) == (1, "fail")
    assert report.splitlines()[1] == HIDDEN
    assert (
        "`lookup_runs` passed on the base's cassettes and fails here on 2 of 4 cases "
        "(`run-0/lookup_order`, `run-2/lookup_order`)."
    ) in report
    assert "Statistical verdict: SHIP" in report  # the PR's cassettes say nothing changed
    assert "⚠️ This PR changes `cassettes`" in report


def test_a_clean_re_baseline_is_no_decision_and_can_land(gated: Path) -> None:
    # README's Quickstart, step 5: the change is intended, so re-baseline that node.
    _write(gated, {"graph.py": DRIFTED_GRAPH})
    assert main(["cassette", "accept", "--nodes", "respond"]) == 0
    for switch in (False, True):
        code, report, outputs = _ci(switch=switch)
        assert (code, outputs["verdict"]) == (0, "needs_human")
        assert report.splitlines()[1] == NO_DECISION
        assert "Read the cassette diff and decide" in report
        assert "code check failed" not in report


def test_runs_pair_by_input_so_a_re_recording_in_another_order_is_still_compared(
    gated: Path,
) -> None:
    _write(gated, {"graph.py": SKIPS_THE_LOOKUP})
    _re_record(reverse=True)  # the order questions are now runs 1 and 3
    _, report, _ = _ci()
    assert report.splitlines()[1] == HIDDEN
    assert "(`run-1/lookup_order`, `run-3/lookup_order`)" in report


def test_a_check_that_also_failed_on_the_base_s_cassettes_is_not_hidden(gated: Path) -> None:
    _write(gated, {"graph.py": DRIFTED_GRAPH})
    _re_record()  # runs 1 and 3 never look the order up, on either side
    code, report, _ = _ci(switch=False)
    assert code == 0
    assert report.splitlines()[1] == NO_DECISION


def test_a_new_failure_the_pr_s_cassettes_still_show_is_not_called_hidden(gated: Path) -> None:
    _write(gated, {"graph.py": SKIPS_THE_LOOKUP})
    _edit_cassettes_without_re_baselining()
    code, report, _ = _ci("--fail-on", "never")
    assert code == 1
    assert report.splitlines()[1] == "## 🔴 lgcov: code check failed"
    assert "hidden" not in report


def test_advisory_mode_reports_a_hidden_failure_and_exits_0(gated: Path) -> None:
    _write(gated, {"graph.py": SKIPS_THE_LOOKUP})
    _re_record()
    code, report, outputs = _ci("--mode", "advisory")
    assert (code, outputs["verdict"]) == (0, "fail")
    assert report.splitlines()[1] == f"{HIDDEN} (advisory, not blocking)"


def test_a_code_check_in_the_base_s_advisory_kpis_is_not_a_hidden_failure(
    gated: Path, tmp_path: Path
) -> None:
    _write(tmp_path / "base", {"lgcov.yaml": CONFIG + "gate: {advisory_kpis: [lookup_runs]}\n"})
    _write(gated, {"graph.py": SKIPS_THE_LOOKUP})
    _re_record()
    code, report, _ = _ci()
    assert code == 0
    assert report.splitlines()[1] == NO_DECISION


def test_the_first_adoption_pr_exits_0_with_nothing_to_compare(gated: Path, tmp_path: Path) -> None:
    for name in CHECKOUT:
        (tmp_path / "base" / name).unlink()
    _write(gated, {"graph.py": SKIPS_THE_LOOKUP})
    _re_record()
    code, report, outputs = _ci()
    assert (code, outputs["verdict"]) == (0, "advisory")
    assert report.splitlines()[1] == f"## ⚠️ {BOOTSTRAP_HEADLINE}"


def test_a_stopped_run_is_not_compared_and_follows_the_switch(gated: Path) -> None:
    # Its trace is cut off, so against the base's cassettes the check would read as newly failed.
    _write(gated, {"graph.py": REWRITES_THE_QUESTION})
    _edit_cassettes_without_re_baselining()
    args = ("--fail-on", "never", "--on-divergence", "fail")
    code, report, outputs = _ci(*args, switch=False)
    assert (code, outputs["verdict"]) == (0, "needs_human")
    assert report.splitlines()[1] == NO_DECISION
    assert "lgcov did not check them." in report
    code, report, outputs = _ci(*args, switch=True)
    assert (code, outputs["verdict"]) == (1, "needs_human")
    assert "code check failed" not in report


def test_a_base_with_no_baseline_has_nothing_to_compare(gated: Path, tmp_path: Path) -> None:
    (tmp_path / "base" / "cassettes" / "baseline.json").unlink()
    _write(gated, {"graph.py": SKIPS_THE_LOOKUP})
    _re_record()
    code, report, _ = _ci(switch=False)
    assert code == 0
    assert report.splitlines()[1] == NO_DECISION
    assert "Could not read" not in report


def test_a_base_baseline_that_cannot_be_read_says_so(gated: Path, tmp_path: Path) -> None:
    _write(tmp_path / "base", {"cassettes/baseline.json": "not json"})
    _write(gated, {"graph.py": SKIPS_THE_LOOKUP})
    _re_record()
    code, report, _ = _ci(switch=False)
    assert code == 0
    assert report.splitlines()[1] == NO_DECISION
    assert "⚠️ Could not read the base's cassettes, so they weren't compared" in report


def _keep_runs(*keep: int) -> None:
    path = Path("cassettes/baseline.json")
    runs = json.loads(path.read_text(encoding="utf-8"))["runs"]
    path.write_text(json.dumps({"runs": [runs[i] for i in keep]}), "utf-8")


ORDER_RUN = 'run-{}: {{"question": "Where is my order?"}}'


def test_runs_the_pr_removed_are_named_and_do_not_block(gated: Path) -> None:
    # The failing runs are gone, so nothing compares them; the comment says which they were.
    _write(gated, {"graph.py": SKIPS_THE_LOOKUP})
    _keep_runs(1, 3)
    code, report, _ = _ci("--fail-on", "never", switch=False)
    assert code == 0
    assert report.splitlines()[1] == NO_DECISION
    assert (
        "⚠️ 2 runs recorded on the base aren't in this PR's `cassettes`, so they weren't checked: "
        f"`{ORDER_RUN.format(0)}`, `{ORDER_RUN.format(2)}`."
    ) in report


def test_one_removed_run_is_named_alongside_a_hidden_failure(gated: Path) -> None:
    # Runs 0 and 2 share an input, so the one left pairs with run 0 and run 2 is the one removed.
    _keep_runs(0, 1, 3)
    _write(gated, {"graph.py": SKIPS_THE_LOOKUP})
    _re_record()
    code, report, _ = _ci()
    assert code == 1
    assert report.splitlines()[1] == HIDDEN
    assert "on 1 of 3 cases (`run-0/lookup_order`)" in report
    assert (
        "⚠️ 1 run recorded on the base isn't in this PR's `cassettes`, so it wasn't checked: "
        f"`{ORDER_RUN.format(2)}`."
    ) in report


def test_a_removed_run_is_named_with_no_taxonomy_too(gated: Path) -> None:
    _keep_runs(0)
    _, report, _ = _ci(taxonomy=False)
    assert "⚠️ 3 runs recorded on the base aren't in this PR's `cassettes`" in report
    assert report.index("`run-1:") < report.index("`run-2:") < report.index("`run-3:")


def test_many_removed_runs_are_counted_and_a_long_input_is_cut_safely(
    gated: Path, tmp_path: Path
) -> None:
    long = "a`b" + "x" * 100
    extra = [{"inputs": {"question": f"{long} {i}"}, "steps": []} for i in range(4)]
    runs = json.loads(BASELINE)["runs"] * 2
    _write(tmp_path / "base", {"cassettes/baseline.json": json.dumps({"runs": [*extra, *runs]})})
    _, report, _ = _ci(taxonomy=False)
    line = next(x for x in report.splitlines() if "recorded on the base" in x)
    assert line.startswith("⚠️ 4 runs recorded on the base aren't")
    assert line.endswith(" and 1 more.")
    assert '`run-0: {"question": "a\'b' in line  # a backtick can't close the code span
    assert "x" * 100 not in line and "…`" in line


def test_nothing_is_named_when_no_run_was_removed(gated: Path) -> None:
    _write(gated, {"graph.py": DRIFTED_GRAPH})
    _re_record(reverse=True)  # another order and new outputs, the same inputs
    _, report, _ = _ci(switch=False)
    assert "⚠️ This PR changes `cassettes`" in report
    assert "recorded on the base" not in report


def test_a_run_the_pr_added_is_not_named_as_removed(gated: Path) -> None:
    runs = json.loads(Path("cassettes/baseline.json").read_text(encoding="utf-8"))["runs"]
    inputs = [*(r["inputs"] for r in runs), {"question": "Do you ship abroad?"}]
    Path("../inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
    assert main(["record", "--inputs", "../inputs.json"]) == 0
    _, report, _ = _ci(switch=False)
    assert "recorded on the base" not in report


def test_a_run_the_pr_added_has_nothing_to_compare(gated: Path, tmp_path: Path) -> None:
    # With no base run to pair it with, an empty trace mustn't read as a pass on the base.
    never = CODE_TAXONOMY.replace("type: node_visited", "type: node_not_visited")
    runs = json.loads(BASELINE)["runs"]
    hours = [r for r in runs if "order" not in str(r["inputs"])] * 4
    files = {"taxonomy.yaml": never, "cassettes/baseline.json": json.dumps({"runs": hours})}
    _write(tmp_path / "base", files)
    _write(gated, files)
    inputs = [r["inputs"] for r in hours] + [next(r for r in runs if r not in hours)["inputs"]]
    Path("../inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
    assert main(["record", "--inputs", "../inputs.json"]) == 0
    code, report, _ = _ci(switch=False)
    assert code == 0
    assert report.splitlines()[1] == NO_DECISION


def test_runs_with_the_same_input_each_pair_with_their_own_base_run(
    gated: Path, tmp_path: Path
) -> None:
    runs = json.loads(BASELINE)["runs"]
    order = next(r for r in runs if "order" in str(r["inputs"]))
    varied = {**order, "steps": [s for s in order["steps"] if s["node"] != "lookup_order"]}
    files = {"cassettes/baseline.json": json.dumps({"runs": [order, varied] * 2})}
    _write(tmp_path / "base", files)
    _write(gated, {**files, "graph.py": SKIPS_THE_LOOKUP})
    _re_record()
    _, report, _ = _ci()
    assert report.splitlines()[1] == HIDDEN
    assert "on 2 of 4 cases (`run-0/lookup_order`, `run-2/lookup_order`)" in report


def test_a_model_judge_is_not_compared_against_the_base_s_cassettes(pr: Path) -> None:
    # Only code checks are free to run twice over, and only their verdicts are facts.
    _edit_cassettes_without_re_baselining()
    code, report, _ = _ci(switch=False)
    assert code == 0
    assert "hidden" not in report
