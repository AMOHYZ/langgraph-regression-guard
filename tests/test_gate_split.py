"""T179: a gate run cut in two. One job runs the PR's code and writes down what it did. Another,
which never runs that code, decides from that file, the base commit and the PR's commit."""

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from lgcov.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
DEMO_GRAPH = (FIXTURES / "demo_graph.py").read_text(encoding="utf-8")
BASELINE = (FIXTURES / "demo_baseline.json").read_text(encoding="utf-8")
CONFIG = "graph_entrypoint: graph.py:graph\ncassette_dir: cassettes\n"
KPI = "answer_states_order_status"
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
    check: {{fail_every: "2", when_input_contains: "order"}}
"""
BANDS = json.dumps({"version": 1, "jev_model": None, "judges": {KPI: {"band": "blocking"}}})
CHECKOUT = {
    "graph.py": DEMO_GRAPH,
    "cassettes/baseline.json": BASELINE,
    "lgcov.yaml": CONFIG,
    "taxonomy.yaml": TAXONOMY,
    "calibration/bands.json": BANDS,
}
RESPOND = '    ctx = state.get("tool_result", "")\n'
CLASSIFY = 'return {"needs_tool": "order" in state["question"].lower()}'
DRIFTED = DEMO_GRAPH.replace("a concise", "a friendly")
RAISES = DEMO_GRAPH.replace(RESPOND, '    raise ValueError("boom")\n')
TIMES_OUT = DEMO_GRAPH.replace(RESPOND, '    raise TimeoutError("model timed out")\n')
SKIPS_THE_LOOKUP = DEMO_GRAPH.replace(CLASSIFY, 'return {"needs_tool": False}')
REWRITES_THE_QUESTION = DEMO_GRAPH.replace(
    CLASSIFY,
    'return {"question": state["question"].lower(), '
    '"needs_tool": "order" in state["question"].lower()}',
)
RE_BASELINED = BASELINE.replace('"tool_result": "order #42: shipped"', '"tool_result": "edited"')
ALL_POLICY = ("lgcov.yaml", "taxonomy.yaml", "calibration/bands.json", "cassettes/baseline.json")
GATED = ("--taxonomy", "taxonomy.yaml", "--fail-on-inconclusive")
# What the evaluator job has: no project install, so neither side's graph could be imported.
NOT_IMPORTABLE = 'raise AssertionError("the evaluator ran the project\'s code")\n'
FILE = Path("../out/candidate.json")
REPORT = Path("../out/report.md")


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
    (tmp_path / "out").mkdir()
    monkeypatch.chdir(tmp_path / "pr")
    return tmp_path / "pr"


def _candidate(*flags: str) -> int:
    args = ["ci", "--report", str(REPORT), "--candidate-out", str(FILE)]
    return main([*args, "--base-dir", "../base", "--policy-dir", "../base", *flags])


def _gate(*flags: str) -> tuple[int, str, dict[str, str], str]:
    """`lgcov ci` as the Action runs it on a PR, after the candidate half if flags say so: the exit
    code, the report, the outputs and coverage.json."""
    outputs, coverage = Path("../out/outputs.txt"), Path("../out/coverage.json")
    outputs.write_text("", encoding="utf-8")
    coverage.unlink(missing_ok=True)
    args = ["ci", "--report", str(REPORT), "--github-output", str(outputs)]
    args += ["--base-dir", "../base", "--policy-dir", "../base", "--coverage-out", str(coverage)]
    code = main([*args, *flags])
    lines = outputs.read_text(encoding="utf-8").splitlines()
    return (
        code,
        REPORT.read_text(encoding="utf-8"),
        {k: v for k, v in (x.split("=", 1) for x in lines)},
        coverage.read_text(encoding="utf-8") if coverage.exists() else "",
    )


def _evaluate(*flags: str) -> tuple[int, str, dict[str, str]]:
    code, report, outputs, _ = _gate("--candidate-in", str(FILE), *flags)
    return code, report, outputs


def _normal(report: str) -> str:
    """Durations differ from run to run. Everything else, like the count of live calls, doesn't."""
    timed = r"(<0\.01|\d+(\.\d+)?)"  # the ⏱ line, and the KPI table's ms/judgment column
    return re.sub(rf"{timed} (s\b|\({timed} total\))", "T", report)


def _both_ways(*flags: str) -> tuple[int, str]:
    """One job, then two, with the project's code out of the second's reach: they must agree."""
    code, report, outputs, coverage = _gate(*flags)
    assert _candidate(*flags) == 0
    graphs = [g for g in (Path("graph.py"), Path("../base/graph.py")) if g.exists()]
    kept = [g.read_text(encoding="utf-8") for g in graphs]
    for graph in graphs:
        graph.write_text(NOT_IMPORTABLE, encoding="utf-8")
    split = _gate("--candidate-in", str(FILE), *flags)
    for graph, text in zip(graphs, kept, strict=True):
        graph.write_text(text, encoding="utf-8")
    assert split[0] == code
    assert split[2] == outputs
    assert _normal(split[1]) == _normal(report)
    assert split[3] == coverage
    return code, report


@dataclass(frozen=True)
class Case:
    pr: dict[str, str] = field(default_factory=dict)
    base: dict[str, str] = field(default_factory=dict)
    gone: tuple[str, ...] = ()  # from the PR's checkout
    gone_from_base: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    code: int = 0
    headline: str = "## 🟢"


NO_DECISION = "## ⚠️ No decision: needs_human"
FAILED = "## 🔴"
HARNESS = "## ⚠️ lgcov: harness error"
CASES = {
    "nothing changed": Case(),
    "drift": Case({"graph.py": DRIFTED}, code=1, headline=FAILED),
    "drift nothing enforces": Case(
        {"graph.py": DRIFTED}, flags=("--fail-on", "never"), headline=NO_DECISION
    ),
    "drift in advisory mode": Case(
        {"graph.py": DRIFTED}, flags=("--mode", "advisory"), headline=FAILED
    ),
    "a node raises": Case({"graph.py": RAISES}, code=1, headline=FAILED),
    "a timeout in a node": Case({"graph.py": TIMES_OUT}, code=2, headline=HARNESS),
    "a graph that won't import": Case(
        {"graph.py": 'raise RuntimeError("boom at import")\n'}, code=2, headline=HARNESS
    ),
    "a graph this PR moved": Case(gone=("graph.py",), code=2, headline=HARNESS),
    "a baseline that can't be read": Case(
        {"cassettes/baseline.json": "not json"}, code=2, headline=HARNESS
    ),
    "a broken lgcov.yaml on the base": Case(
        base={"lgcov.yaml": "tier: every-week\n" + CONFIG}, code=2, headline=HARNESS
    ),
    "a broken taxonomy on the base": Case(
        base={"taxonomy.yaml": "not: a taxonomy\n"},
        flags=("--taxonomy", "taxonomy.yaml"),
        code=2,
        headline=HARNESS,
    ),
    "a divergence, flagged": Case({"graph.py": REWRITES_THE_QUESTION}, code=1, headline=FAILED),
    "a divergence that stops the run": Case(
        {"graph.py": REWRITES_THE_QUESTION},
        flags=("--on-divergence", "fail"),
        code=1,
        headline=FAILED,
    ),
    "a budget hit": Case(
        {"graph.py": DRIFTED}, flags=("--budget-seconds", "0"), code=1, headline=FAILED
    ),
    "a re-baseline": Case({"cassettes/baseline.json": RE_BASELINED}, headline=NO_DECISION),
    "a whitespace edit to the cassette": Case(
        {"cassettes/baseline.json": BASELINE + "\n"}, headline=NO_DECISION
    ),
    "the PR edits its policy": Case(
        {"lgcov.yaml": CONFIG + "ignore_paths: [/answer]\n", "taxonomy.yaml": "edited: true\n"},
    ),
    "the PR edits its labels": Case({"calibration/notes.txt": "not a label file\n"}),
    "coverage under the floor": Case(
        {"graph.py": SKIPS_THE_LOOKUP},
        base={"lgcov.yaml": CONFIG + "thresholds: {min_node_coverage: 1.0}\n"},
        code=1,
        headline=FAILED,
    ),
    "a KPI the judge can't settle": Case(flags=GATED, code=1, headline=NO_DECISION),
    "a first adoption PR": Case(
        {"graph.py": DRIFTED}, gone_from_base=ALL_POLICY, flags=GATED, headline="## ⚠️ Advisory"
    ),
}


@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_two_jobs_come_to_what_one_job_does(pr: Path, tmp_path: Path, case: Case) -> None:
    _write(pr, case.pr)
    _write(tmp_path / "base", case.base)
    for name in case.gone:
        (pr / name).unlink()
    for name in case.gone_from_base:
        (tmp_path / "base" / name).unlink()
    code, report = _both_ways(*case.flags)
    assert code == case.code
    assert report.splitlines()[1].startswith(case.headline)


ADDS_A_NODE = DEMO_GRAPH.replace(
    'b.add_edge("respond", END)',
    'b.add_node("extra", respond)\nb.add_edge("respond", "extra")\nb.add_edge("extra", END)',
)


def test_the_graph_diff_and_lint_come_from_the_candidate_job(pr: Path) -> None:
    # The base's graph runs there too, since this job can't import it.
    _write(pr, {"graph.py": ADDS_A_NODE})
    code, report = _both_ways()
    assert code == 1
    assert "| + | node | extra |" in report
    assert "🧹 Lint:" in report


def test_the_time_on_the_report_counts_both_jobs(pr: Path) -> None:
    _forged(seconds=1000.0, agent_seconds=500.0)
    assert "⏱ 1000 s · agent 500 s (" in _evaluate()[1]


def test_a_mask_on_the_base_applies_in_the_job_that_decides(pr: Path, tmp_path: Path) -> None:
    _write(pr, {"graph.py": DRIFTED})
    _write(tmp_path / "base", {"lgcov.yaml": CONFIG + "ignore_paths: [/answer]\n"})
    code, report = _both_ways()
    assert code == 0
    assert "No cassette drift (2 runs)." in report


def test_a_run_that_stopped_is_not_checked_rather_than_drifted(pr: Path) -> None:
    _write(pr, {"graph.py": REWRITES_THE_QUESTION})
    _, report = _both_ways("--on-divergence", "fail", "--fail-on", "never")
    assert "Not checked (on-divergence: fail): run 1 from `lookup_order`" in report
    assert "| `respond` |" not in report


def test_a_budget_hit_reaches_the_report(pr: Path) -> None:
    _write(pr, {"graph.py": DRIFTED})
    _, report = _both_ways("--budget-seconds", "0")
    assert "budget hit at node `respond` after" in report
    assert "Not checked (budget hit): run 2 from `respond`" in report


def test_a_node_that_raises_is_named_as_the_root(pr: Path) -> None:
    _write(pr, {"graph.py": RAISES})
    _, report = _both_ways()
    assert "Node `respond` raised ValueError: boom" in report
    assert "root drift: 1" in report


def test_a_timeout_names_its_node_and_is_not_a_regression(pr: Path) -> None:
    _write(pr, {"graph.py": TIMES_OUT})
    _, report = _both_ways()
    assert "⚠️ Harness error (transport) in node `respond`, not a regression" in report
    assert "root drift: 0" in report


def test_the_candidate_half_says_how_long_it_took(pr: Path) -> None:
    _write(pr, {"graph.py": DRIFTED})
    assert _candidate() == 0
    body = json.loads(FILE.read_text("utf-8"))
    assert body["seconds"] > body["agent_seconds"] > 0


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


def test_two_jobs_catch_a_code_check_a_re_baseline_hides(pr: Path, tmp_path: Path) -> None:
    files = {
        "taxonomy.yaml": CODE_TAXONOMY,
        "cassettes/baseline.json": json.dumps({"runs": json.loads(BASELINE)["runs"] * 2}),
        "calibration/bands.json": json.dumps({"version": 1, "jev_model": None, "judges": {}}),
    }
    _write(tmp_path / "base", files)
    _write(pr, {**files, "graph.py": SKIPS_THE_LOOKUP})
    runs = json.loads(files["cassettes/baseline.json"])["runs"]
    Path("../inputs.json").write_text(json.dumps([r["inputs"] for r in runs]), encoding="utf-8")
    assert main(["record", "--inputs", "../inputs.json"]) == 0
    code, report = _both_ways(*GATED)
    assert code == 1
    assert report.splitlines()[1] == "## 🔴 code check failed (hidden by a re-baseline in this PR)"


MESSAGES_GRAPH = """\
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph


def model(state: MessagesState) -> MessagesState:
    call = {"name": "search", "args": {"q": "x"}, "id": f"call_{uuid4().hex}"}
    return {"messages": [AIMessage("", id=f"run-{uuid4()}", tool_calls=[call])]}


def tools(state: MessagesState) -> MessagesState:
    call = state["messages"][-1].tool_calls[0]
    return {"messages": [ToolMessage("found", tool_call_id=call["id"], name=call["name"])]}


g = StateGraph(MessagesState)
g.add_node("model", model)
g.add_node("tools", tools)
g.add_edge(START, "model")
g.add_edge("model", "tools")
g.add_edge("tools", END)
graph = g.compile()
"""
COMMAND_GRAPH = """\
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command


class S(TypedDict, total=False):
    x: int
    y: str


def a(state: S) -> Command[Literal["b", "c"]]:
    return Command(goto="b", update={"x": 2})


def b(state: S) -> S:
    return {"y": "same"}


def c(state: S) -> S:
    return {"y": "same"}


g = StateGraph(S)
g.add_node("a", a)
g.add_node("b", b)
g.add_node("c", c)
g.add_edge(START, "a")
g.add_edge("b", END)
g.add_edge("c", END)
graph = g.compile()
"""
FAN_OUT_GRAPH = """\
from fixtures.replay_audit.send_fanout import build

graph = build(("a", "b"))
"""
USER = {"messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.parametrize(
    ("graph", "inputs", "edit"),
    [
        (MESSAGES_GRAPH, USER, ('"found"', '"changed"')),
        (COMMAND_GRAPH, {"x": 1}, ('goto="b"', 'goto="c"')),
        (FAN_OUT_GRAPH, {}, ('("a", "b")', '("a", "c")')),
    ],
    ids=["messages", "a Command", "Send fan-out"],
)
def test_every_kind_of_step_crosses_the_file(
    pr: Path, tmp_path: Path, graph: str, inputs: dict[str, object], edit: tuple[str, str]
) -> None:
    _write(pr, {"graph.py": graph})
    Path("../inputs.json").write_text(json.dumps([inputs]), encoding="utf-8")
    assert main(["record", "--inputs", "../inputs.json"]) == 0
    recorded = (pr / "cassettes" / "baseline.json").read_text(encoding="utf-8")
    _write(tmp_path / "base", {"graph.py": graph, "cassettes/baseline.json": recorded})
    assert _both_ways()[0] == 0
    _write(pr, {"graph.py": graph.replace(*edit)})
    assert _both_ways()[0] == 1


def _rejected(result: tuple[int, str, dict[str, str]], why: str) -> None:
    code, report, outputs = result
    assert (code, outputs["verdict"], outputs["exit-code"]) == (2, "error", "2")
    assert report.splitlines()[1] == "## ⚠️ lgcov: harness error, not a regression"
    assert "🟢" not in report and "**pass**" not in report
    assert why in report


def _forged(**changes: object) -> None:
    assert _candidate() == 0
    FILE.write_text(json.dumps({**json.loads(FILE.read_text("utf-8")), **changes}), "utf-8")


def test_no_file_from_the_candidate_job_gives_no_verdict(pr: Path) -> None:
    _rejected(_evaluate(), "The candidate job left no result")


def test_a_file_that_is_not_json_gives_no_verdict(pr: Path) -> None:
    FILE.write_text("{", encoding="utf-8")
    _rejected(_evaluate(), "The candidate job's result can't be read")


@pytest.mark.parametrize(
    ("changes", "why"),
    [
        ({"verdict": "pass"}, "The candidate job's result can't be read"),
        ({"schema": 99}, "The candidate job's result can't be read"),
        ({"head": None}, "The candidate job's result can't be read"),
        ({"commit": "c" * 40}, "The candidate job ran on `ccccccc`, and this job reads the PR at"),
        ({"tier": "nightly"}, "The candidate job ran tier `nightly`, and this job's is `pr`"),
        ({"runs": []}, "The candidate job's result doesn't match this PR's cassette"),
    ],
    ids=["a verdict", "another schema", "no graph", "another commit", "another tier", "no runs"],
)
def test_a_file_this_job_cannot_use_gives_no_verdict(
    pr: Path, changes: dict[str, object], why: str
) -> None:
    _forged(**changes)
    _rejected(_evaluate(), why)


def test_a_file_that_breaks_the_gate_gives_no_verdict(pr: Path) -> None:
    # Valid steps, nested past what comparing them can walk: whichever part gives up, no verdict.
    assert _candidate() == 0
    body = json.loads(FILE.read_text("utf-8"))
    body["runs"][0]["steps"][0]["output"] = "NESTED"
    FILE.write_text(json.dumps(body).replace('"NESTED"', "[" * 5000 + "]" * 5000), "utf-8")
    _rejected(_evaluate(), "The candidate job's result can't be")


def test_drift_comes_from_this_job_whatever_the_file_says_about_its_calls(pr: Path) -> None:
    _write(pr, {"graph.py": DRIFTED})
    assert _candidate() == 0
    body = json.loads(FILE.read_text("utf-8"))
    for run in body["runs"]:
        for call in run["calls"]:
            call["how"] = "served"
    FILE.write_text(json.dumps(body), encoding="utf-8")
    code, report, outputs = _evaluate()
    assert (code, outputs["verdict"]) == (1, "fail")
    assert "| `respond` | root | 2/2 |" in report


def test_what_is_expected_comes_from_the_pr_s_commit_and_not_the_file(pr: Path) -> None:
    assert _candidate() == 0
    _write(pr, {"cassettes/baseline.json": RE_BASELINED})
    code, report, _ = _evaluate()
    assert code == 1
    assert "⚠️ This PR changes `cassettes`" in report


def test_the_known_limit_a_file_reporting_the_recorded_outputs_passes(pr: Path) -> None:
    # The PR's code wrote this file, and this job can't re-run that code to check it. It takes no
    # more than code that recognises a recorded input: docs/trust-boundary.md.
    _write(pr, {"graph.py": DRIFTED})
    runs = json.loads(BASELINE)["runs"]
    _forged(runs=[{"steps": r["steps"], "calls": [], "halt": None} for r in runs])
    code, report, outputs = _evaluate()
    assert (code, outputs["verdict"]) == (0, "pass")
    assert "every node served" in report


def test_the_evaluator_prints_nothing_the_pr_s_code_wrote(
    pr: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The runner reads `::` commands from a step's output, so this would set an output of its own.
    command = "::set-output name=exit-code::0"
    _write(pr, {"graph.py": RAISES})
    _forged(crash=f"Node `respond` raised ValueError: \n{command}\n")
    capsys.readouterr()
    code, report, _ = _evaluate()
    assert code == 1 and command in report
    out, err = capsys.readouterr()
    assert command not in out and command not in err


def test_one_job_still_prints_the_report(pr: Path, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    assert _gate()[0] == 0
    assert "<!-- lgcov-report -->" in capsys.readouterr().out


def test_the_evaluator_s_harness_error_comes_first(pr: Path) -> None:
    _write(pr, {"graph.py": DRIFTED})
    assert _candidate() == 0
    _rejected(_evaluate("--harness-error", "Could not check out the base commit."), "Could not")


def test_the_evaluator_s_result_verifies_and_keeps_its_verdict(
    pr: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"head": {"sha": "b" * 40}}}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    _write(pr, {"graph.py": DRIFTED})
    assert _candidate() == 0
    result = Path("../out/result.json")
    assert _evaluate("--result-out", str(result))[0] == 1
    before = REPORT.read_text(encoding="utf-8")
    args = ["verify-result", "--result", str(result), "--report", str(REPORT)]
    assert main([*args, "--policy-dir", "../base", "--base-sha", "", "--head-sha", "b" * 40]) == 1
    assert REPORT.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "graph",
    [DEMO_GRAPH, DRIFTED, RAISES, "nothing to import("],
    ids=["clean", "drift", "raises", "syntax"],
)
def test_the_candidate_half_exits_0_and_writes_no_report_or_verdict(pr: Path, graph: str) -> None:
    _write(pr, {"graph.py": graph})
    assert _candidate() == 0
    assert FILE.is_file() and not REPORT.exists()
    assert "verdict" not in json.loads(FILE.read_text("utf-8"))


def test_the_candidate_half_judges_nothing(pr: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def judged(*_: object) -> None:
        raise AssertionError("the job that runs the PR's code judged it")

    monkeypatch.setattr("lgcov.cli._ci_gate", judged)
    assert _candidate(*GATED) == 0
    assert json.loads(FILE.read_text("utf-8"))["harness_error"] == ""


def test_the_candidate_half_reads_the_base_s_policy(pr: Path) -> None:
    # Its tier and what it masks come from the base, as the gate's do.
    _write(pr, {"graph.py": DRIFTED, "lgcov.yaml": CONFIG + "tier: nightly\n"})
    assert _candidate() == 0
    assert json.loads(FILE.read_text("utf-8"))["tier"] == "pr"


def test_a_harness_error_in_the_candidate_job_reaches_the_report(pr: Path) -> None:
    assert _candidate("--harness-error", "Installing your project failed (exit 1)") == 0
    _rejected(_evaluate(), "Installing your project failed (exit 1)")


def test_steps_the_file_cannot_hold_give_no_verdict_rather_than_a_crash(
    pr: Path, tmp_path: Path
) -> None:
    # A tuple as a key: json.dumps refuses it with no way round. Masked, comparing never reads it.
    tuple_keyed = 'return {"tool_result": {("a", "b"): 1}}'
    _write(
        pr,
        {
            "graph.py": DEMO_GRAPH.replace(
                'return {"tool_result": "order #42: shipped"}', tuple_keyed
            )
        },
    )
    _write(tmp_path / "base", {"lgcov.yaml": CONFIG + "ignore_paths: [/tool_result]\n"})
    assert _candidate() == 0
    _rejected(_evaluate(), "this run's steps can't be saved")


def test_a_flag_for_each_half_at_once_is_refused(pr: Path) -> None:
    with pytest.raises(SystemExit) as stopped:
        _candidate("--candidate-in", str(FILE))
    assert stopped.value.code == 2


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return done.stdout.decode().strip()


def _repo(path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Commit everything at path to a new repo, away from your own git config; its HEAD."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "lgcov test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")
    _git(path, "init", "-q")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "--allow-empty", "-m", "start")
    return _git(path, "rev-parse", "HEAD")


def test_the_commit_the_candidate_ran_on_has_to_be_the_one_read_here(
    pr: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _repo(pr, monkeypatch)
    assert _candidate() == 0
    code, report, _ = _evaluate()
    assert code == 0
    assert f"candidate `{head[:7]}`" in report
    _git(pr, "commit", "-q", "--allow-empty", "-m", "another commit")
    _rejected(_evaluate(), f"The candidate job ran on `{head[:7]}`, and this job reads the PR at `")


def test_a_commit_that_moves_under_the_candidate_job_gives_no_verdict(
    pr: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    moving = (FIXTURES / "moving_graph.py").read_text(encoding="utf-8")
    _write(pr, {"graph.py": moving})
    head = _repo(pr, monkeypatch)
    monkeypatch.setenv("LGCOV_MOVE_REPO", str(pr))  # respond commits there each time it runs
    assert _candidate() == 0
    monkeypatch.delenv("LGCOV_MOVE_REPO")
    _rejected(_evaluate(), f"the candidate was `{head[:7]}` at the start and is `")
