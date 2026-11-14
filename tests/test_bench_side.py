"""T166: `lgcov bench` runs each side in its own process, so base and head each import their own
modules. In one process, Python hands head whatever base loaded. T169: `--keep-runs` saves each
side's runs as they finish, so a crash can't lose what was already paid for."""

import contextlib
import io
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from lgcov.attribution import Benchmark
from lgcov.cli import CANT_IMPORT, _load_runs, _run_side, _SideFailed, main

LINEAR = Path(__file__).parent / "fixtures" / "bench" / "linear"
GRAPH = """\
import os
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import helper


class S(TypedDict, total=False):
    q: str
    b: str
    c: str


def _called(name: str) -> None:
    print(f"ROW:{name} ran")
    with open(os.environ["BENCH_TEST_CALLS"], "a", encoding="utf-8") as f:
        f.write(f"ROW:{name}\\n")


def a(state: S) -> S:
    _called("a")
    return {"q": state["q"].strip()}


def b(state: S) -> S:
    _called("b")
    return {"b": f"{helper.PROMPT}:{state['q']}"}


def c(state: S) -> S:
    _called("c")
    return {"c": state["b"].upper()}


g = StateGraph(S)
g.add_node("a", a)
g.add_node("b", b)
g.add_node("c", c)
g.add_edge(START, "a")
g.add_edge("a", "b")
g.add_edge("b", "c")
g.add_edge("c", END)
graph = g.compile()
"""
MESSAGES_GRAPH = """\
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command

import helper


async def a(state: MessagesState) -> Command:
    call = {"name": "search", "args": {"q": "x"}, "id": f"call_{uuid4().hex}"}
    return Command(
        goto="tools",
        update={"messages": [AIMessage("", id=f"run-{uuid4()}", tool_calls=[call])]},
    )


def tools(state: MessagesState) -> MessagesState:
    call = state["messages"][-1].tool_calls[0]
    return {"messages": [ToolMessage("found", tool_call_id=call["id"], name=call["name"])]}


async def b(state: MessagesState) -> MessagesState:
    return {"messages": [AIMessage(helper.PROMPT, id=f"run-{uuid4()}")]}


async def c(state: MessagesState) -> MessagesState:
    text = str(state["messages"][-1].content).upper()
    return {"messages": [AIMessage(text, id=f"run-{uuid4()}")]}


g = StateGraph(MessagesState)
g.add_node("a", a)
g.add_node("tools", tools)
g.add_node("b", b)
g.add_node("c", c)
g.add_edge(START, "a")
g.add_edge("tools", "b")
g.add_edge("b", "c")
g.add_edge("c", END)
graph = g.compile()
"""
ROW = """\
graph: {graph}
repo: ../repo
base_ref: {name}-base
head_ref: {name}-head
cases:
  - {case}
expected:
  root: [b]
  propagated: [c]
{extra}"""
Files = dict[str, str]


def _helper(prompt: str) -> str:
    return f'PROMPT = "{prompt}"\n'


@dataclass(frozen=True)
class _Run:
    code: int
    rows: dict[str, dict[str, Any]]
    err: str
    calls: tuple[str, ...]
    kept: Path
    out: str = ""


class _Project:
    """A git repo with a base and a head tag per row, and a benchmark file for each row."""

    def __init__(self, root: Path) -> None:
        self.repo, self.bench = root / "repo", root / "bench"
        self.repo.mkdir()
        self.bench.mkdir()
        self._git("init", "-q")

    def _git(self, *args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(self.repo), *args],
            check=True,
            capture_output=True,
        )

    def _snapshot(self, tag: str, files: Files) -> None:
        # The whole tree is this side's: nothing left over from another row can be imported.
        self._git("rm", "-rqf", "--ignore-unmatch", ".")
        for name, text in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "--allow-empty", "-m", tag)
        self._git("tag", tag)

    def row(
        self,
        name: str,
        base: Files,
        head: Files,
        graph: str = "graph.py:graph",
        case: str = '{q: " hi "}',
        extra: str = "",
    ) -> None:
        self._snapshot(f"{name}-base", base)
        self._snapshot(f"{name}-head", head)
        text = ROW.format(name=name, graph=graph, case=case, extra=extra)
        (self.bench / f"{name}.yaml").write_text(text, encoding="utf-8")


@pytest.fixture(scope="session")
def bench(tmp_path_factory: pytest.TempPathFactory) -> _Run:
    """One `lgcov bench` over one project, shared by the tests below: a row costs up to three
    Python processes, each of which spends its time importing langgraph.

    An installed copy of the helper is on the path throughout. In one process that's silently
    wrong: both sides get the installed copy, so nothing drifts."""
    root = tmp_path_factory.mktemp("bench_side")
    project = _Project(root)

    def graph(row: str) -> str:
        return GRAPH.replace("ROW:", f"{row}:")

    v1, v2 = _helper("v1"), _helper("v2")
    project.row(
        "own_helper",
        {"graph.py": graph("own_helper"), "helper.py": v1},
        {"graph.py": graph("own_helper"), "helper.py": v2},  # only the helper differs
    )

    # open_deep_research's shape: a namespace package under src/, no __init__.py.
    def src(row: str, helper: str) -> Files:
        text = graph(row).replace("import helper\n", "from pkg import helper\n")
        return {"src/pkg/graph.py": text, "src/pkg/helper.py": helper}

    project.row(
        "src_layout",
        src("src_layout", v1),
        src("src_layout", v2),
        graph="src/pkg/graph.py:graph",
        extra="pythonpath: [src]\n",
    )
    project.row(
        "no_pythonpath",
        src("no_pythonpath", v1),
        src("no_pythonpath", v2),
        graph="src/pkg/graph.py:graph",
    )
    no_helper = GRAPH.replace("import helper\n", "import only_on_the_other_side\n")
    project.row(
        "bad_base",
        {"graph.py": no_helper},
        {"graph.py": no_helper, "only_on_the_other_side.py": ""},
    )
    head_bad = no_helper.replace("ROW:", "bad_head:")
    project.row(
        "bad_head",
        {"graph.py": head_bad, "only_on_the_other_side.py": ""},
        {"graph.py": head_bad},
    )
    project.row(
        "not_a_graph",
        {"graph.py": graph("not_a_graph"), "helper.py": v1},
        {"graph.py": graph("not_a_graph"), "helper.py": v1},
        graph="graph.py:nothing_here",
    )
    # Head's `c` crashes on the second case only, so head has a finished run to keep.
    crashes = graph("crashes").replace(
        '_called("c")\n',
        '_called("c")\n'
        '    if "BOOM" in state["b"].upper():\n'
        '        raise ValueError("bad draft")\n',
    )
    project.row(
        "crashes",
        {"graph.py": graph("crashes"), "helper.py": v1},
        {"graph.py": crashes, "helper.py": v1},
        case="{q: one}\n  - {q: boom}",
    )
    # Base's `a` crashes on the second case: one run finished, and the other is lost.
    crashes_early = graph("base_crashes").replace(
        '_called("a")\n',
        '_called("a")\n    if state["q"] == "boom":\n        raise ValueError("bad case")\n',
    )
    project.row(
        "base_crashes",
        {"graph.py": crashes_early, "helper.py": v1},
        {"graph.py": crashes_early, "helper.py": v1},
        case="{q: one}\n  - {q: boom}",
    )
    project.row(
        "messages",
        {"graph.py": MESSAGES_GRAPH, "helper.py": v1},
        {"graph.py": MESSAGES_GRAPH, "helper.py": v2},
        case='{messages: [{role: user, content: "hi"}]}',
    )

    installed = root / "installed"
    installed.mkdir()
    (installed / "helper.py").write_text(_helper("installed"), encoding="utf-8")
    calls = root / "calls.log"
    md, results = root / "bench.md", root / "results.json"
    out, err = io.StringIO(), io.StringIO()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PYTHONPATH", str(installed))
        patch.setenv("BENCH_TEST_CALLS", str(calls))
        patch.syspath_prepend(str(installed))
        patch.chdir(root)  # --keep-runs is relative here, and each side runs from its own folder
        args = ["--out", str(md), "--json", str(results), "--keep-runs", "kept"]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["bench", str(project.bench), *args])
    rows = json.loads(results.read_text(encoding="utf-8"))["benchmarks"]
    logged = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    rows_by_name = {r["name"]: r for r in rows}
    return _Run(code, rows_by_name, err.getvalue(), tuple(logged), root / "kept", out.getvalue())


@pytest.mark.slow
def test_each_side_imports_its_own_helper_whatever_is_installed(bench: _Run) -> None:
    row = bench.rows["own_helper"]
    assert not row["error"]
    assert (row["found_root"], row["found_propagated"]) == (["b"], ["c"])


@pytest.mark.slow
def test_pythonpath_in_the_benchmark_file_reaches_a_src_layout(bench: _Run) -> None:
    row = bench.rows["src_layout"]
    assert not row["error"]
    assert (row["found_root"], row["found_propagated"]) == (["b"], ["c"])


@pytest.mark.slow
def test_a_src_layout_with_no_pythonpath_can_t_import(bench: _Run) -> None:
    assert "cannot import the head side" in bench.rows["no_pythonpath"]["error"]
    assert "No module named 'pkg'" in bench.rows["no_pythonpath"]["error"]


@pytest.mark.slow
def test_a_bad_import_on_base_names_base(bench: _Run) -> None:
    assert "bad_base: cannot import the base side" in bench.err
    assert "No module named 'only_on_the_other_side'" in bench.err
    assert "base side" in bench.rows["bad_base"]["error"]


@pytest.mark.slow
def test_a_bad_import_on_head_names_head_and_pays_for_nothing(bench: _Run) -> None:
    # Head can't import, so recording base first would be money spent for no result.
    assert "bad_head: cannot import the head side" in bench.err
    assert "head side" in bench.rows["bad_head"]["error"]
    assert not [c for c in bench.calls if c.startswith("bad_head:")]


@pytest.mark.slow
def test_a_target_that_is_not_a_graph_names_the_side(bench: _Run) -> None:
    assert "not_a_graph: cannot import the head side" in bench.err


@pytest.mark.slow
def test_a_bad_import_exits_2_after_the_other_rows_ran_and_the_report_is_written(
    bench: _Run,
) -> None:
    assert bench.code == 2
    assert sorted(bench.rows) == sorted(
        [
            "own_helper",
            "src_layout",
            "no_pythonpath",
            "bad_base",
            "bad_head",
            "not_a_graph",
            "crashes",
            "base_crashes",
            "messages",
        ]
    )
    assert "own_helper:a" in bench.calls  # a row after the first bad one still ran


@pytest.mark.slow
def test_a_node_that_crashes_is_an_error_row_and_names_the_side(bench: _Run) -> None:
    # A report, not a gate: only a bad import changes the exit code.
    error = bench.rows["crashes"]["error"]
    assert "head side" in error and "bad draft" in error
    assert "crashes: cannot import" not in bench.err


@pytest.mark.slow
def test_async_nodes_and_messages_cross_from_one_side_to_the_other(bench: _Run) -> None:
    # open_deep_research's shape: base's run reaches head as a file, with messages in it.
    row = bench.rows["messages"]
    assert not row["error"]
    assert (row["found_root"], row["found_propagated"]) == (["b"], ["c"])


# The side's own work, in this process: quick enough for `-m "not slow"`.
def test_the_side_says_why_it_can_t_import(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from lgcov.bench_side import side_main

    assert side_main(["check", f"{tmp_path / 'missing.py'}:graph"]) == CANT_IMPORT
    assert "missing.py" in capsys.readouterr().err


def test_the_side_can_check_a_graph() -> None:
    from lgcov.bench_side import side_main

    assert side_main(["check", f"{LINEAR / 'base.py'}:graph"]) == 0


def test_a_run_crosses_from_one_side_to_the_other_as_a_file(tmp_path: Path) -> None:
    from lgcov.bench_side import side_main

    cases, runs, drifted = (tmp_path / f for f in ("cases.json", "runs.json", "drifted.json"))
    cases.write_text('[{"question": "one"}, {"question": "two"}]', encoding="utf-8")
    base, head = f"{LINEAR / 'base.py'}:graph", f"{LINEAR / 'head.py'}:graph"
    assert side_main(["record", base, str(cases), str(runs)]) == 0
    assert [r.inputs["question"] for r in _load_runs(str(runs))] == ["one", "two"]
    assert side_main(["replay", head, str(runs), str(drifted), str(tmp_path / "head.json")]) == 0
    assert [sorted(names) for names in json.loads(drifted.read_text(encoding="utf-8"))] == [
        ["b", "c"],
        ["b", "c"],
    ]


def test_a_crash_after_the_import_is_exit_1_and_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from lgcov.bench_side import side_main

    head = (LINEAR / "base.py").read_text(encoding="utf-8")
    graph = tmp_path / "graph.py"
    graph.write_text(head, encoding="utf-8")
    cases = tmp_path / "cases.json"
    cases.write_text("not json", encoding="utf-8")
    assert side_main(["record", f"{graph}:graph", str(cases), str(tmp_path / "out")]) == 1
    assert "JSONDecodeError" in capsys.readouterr().err


# T169: --keep-runs.
def _inputs(path: Path) -> list[object]:
    return [run.inputs["q"] for run in _load_runs(str(path))]


@pytest.mark.slow
def test_each_side_s_runs_are_kept(bench: _Run) -> None:
    base, head = (
        _load_runs(str(bench.kept / "own_helper" / "base.json")),
        _load_runs(str(bench.kept / "own_helper" / "head.json")),
    )
    assert [s.node for s in base[0].steps] == ["a", "b", "c"]
    assert [s.node for s in head[0].steps] == ["a", "b", "c"]
    assert base[0].steps[1].output == {"b": "v1:hi"}
    assert head[0].steps[1].output == {"b": "v2:hi"}


@pytest.mark.slow
def test_head_crashing_leaves_base_s_file_intact(bench: _Run) -> None:
    assert "head side" in bench.rows["crashes"]["error"]
    assert _inputs(bench.kept / "crashes" / "base.json") == ["one", "boom"]
    assert _inputs(bench.kept / "crashes" / "head.json") == ["one"]  # the run that finished
    assert sorted(f.name for f in (bench.kept / "crashes").iterdir()) == ["base.json", "head.json"]


@pytest.mark.slow
def test_base_crashing_keeps_the_runs_that_finished(bench: _Run) -> None:
    assert "base side" in bench.rows["base_crashes"]["error"]
    assert _inputs(bench.kept / "base_crashes" / "base.json") == ["one"]
    assert not (bench.kept / "base_crashes" / "head.json").exists()


@pytest.mark.slow
def test_a_bad_import_keeps_nothing(bench: _Run) -> None:
    assert not (bench.kept / "bad_head").exists()
    assert not (bench.kept / "bad_base" / "base.json").exists()


@pytest.mark.slow
def test_kept_messages_load_back_as_messages(bench: _Run) -> None:
    head = _load_runs(str(bench.kept / "messages" / "head.json"))
    assert isinstance(head[0].steps[0].output["messages"][0], AIMessage)


CRASHES_ON_TWO = """\
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class S(TypedDict, total=False):
    q: str
    r: str


def a(state: S) -> S:
    if state["q"] == "two":
        raise ValueError("bad case")
    return {"r": state["q"].upper()}


g = StateGraph(S)
g.add_node("a", a)
g.add_edge(START, "a")
g.add_edge("a", END)
graph = g.compile()
"""


def _cases(tmp_path: Path) -> Path:
    cases = tmp_path / "cases.json"
    cases.write_text('[{"q": "one"}, {"q": "two"}]', encoding="utf-8")
    return cases


def test_record_saves_each_run_as_it_finishes(tmp_path: Path) -> None:
    from lgcov.bench_side import side_main

    graph, out = tmp_path / "graph.py", tmp_path / "base.json"
    graph.write_text(CRASHES_ON_TWO, encoding="utf-8")
    assert side_main(["record", f"{graph}:graph", str(_cases(tmp_path)), str(out)]) == 1
    assert _inputs(out) == ["one"]
    assert sorted(f.name for f in tmp_path.iterdir()) == ["base.json", "cases.json", "graph.py"]


def test_replay_saves_each_run_as_it_finishes(tmp_path: Path) -> None:
    from lgcov.bench_side import side_main

    fine = tmp_path / "fine.py"
    fine.write_text(CRASHES_ON_TWO.replace('"two"', '"never"'), encoding="utf-8")
    crashes = tmp_path / "crashes.py"
    crashes.write_text(CRASHES_ON_TWO, encoding="utf-8")
    runs, drifted, head = (tmp_path / f for f in ("base.json", "drifted.json", "head.json"))
    assert side_main(["record", f"{fine}:graph", str(_cases(tmp_path)), str(runs)]) == 0
    args = [f"{crashes}:graph", str(runs), str(drifted), str(head)]
    assert side_main(["replay", *args]) == 1
    assert _inputs(runs) == ["one", "two"]  # base's is untouched
    assert _inputs(head) == ["one"]


def test_a_run_already_kept_is_never_overwritten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # It was paid for. Refused before anything runs, so this test starts no process.
    taken = tmp_path / "kept" / "linear" / "base.json"
    taken.parent.mkdir(parents=True)
    taken.write_text("a paid recording", encoding="utf-8")
    md = tmp_path / "bench.md"
    bench_dir = str(LINEAR.parent)
    args = ["--only", "linear", "--keep-runs", str(tmp_path / "kept"), "--out", str(md)]
    code = main(["bench", bench_dir, *args, "--json", str(tmp_path / "r.json")])
    assert code == 2
    assert "base.json" in capsys.readouterr().err
    assert taken.read_text(encoding="utf-8") == "a paid recording"
    assert not md.exists()


# What a side prints reaches you: open_deep_research's base prints the only text that says why a
# researcher failed. On stderr, because `lgcov bench`'s stdout is the report.
@pytest.mark.slow
def test_what_each_side_prints_reaches_stderr_named_by_side(bench: _Run) -> None:
    assert "[base] own_helper:a ran" in bench.err
    assert "[head] own_helper:b ran" in bench.err
    assert "own_helper:a ran" not in bench.out


@pytest.mark.slow
def test_what_a_side_printed_before_it_crashed_reaches_stderr_too(bench: _Run) -> None:
    assert "[head] crashes:c ran" in bench.err


def _row(tmp_path: Path) -> Benchmark:
    return Benchmark("x", "g.py:g", ({},), frozenset(), frozenset(), tmp_path)


def _side_printing(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str, stderr: str = ""
) -> None:
    done = subprocess.CompletedProcess[str]([], returncode, stdout, stderr)
    monkeypatch.setattr("lgcov.cli.subprocess.run", lambda *_args, **_kwargs: done)


def test_each_printed_line_is_named_by_its_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _side_printing(monkeypatch, 0, "Other error in reflection phase: 429\nanother line\n")
    _run_side(_row(tmp_path), "base", tmp_path, "check", "g.py:g")
    captured = capsys.readouterr()
    assert captured.err == "[base] Other error in reflection phase: 429\n[base] another line\n"
    assert captured.out == ""


def test_a_failing_side_still_shows_what_it_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _side_printing(monkeypatch, 1, "printed first\n", "ValueError: bad draft\n")
    with pytest.raises(_SideFailed, match="head side: ValueError: bad draft"):
        _run_side(_row(tmp_path), "head", tmp_path, "check", "g.py:g")
    assert capsys.readouterr().err == "[head] printed first\n"


def test_a_side_that_prints_nothing_adds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _side_printing(monkeypatch, 0, "")
    _run_side(_row(tmp_path), "base", tmp_path, "check", "g.py:g")
    assert capsys.readouterr().err == ""


# T173: --live runs every head node, so base against base measures the model's own variation.
NOISY = """\
import random
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class S(TypedDict, total=False):
    q: str
    r: str


def a(state: S) -> S:
    return {"r": f"{state['q']}:{random.random()}"}  # a live model: a new answer each time


g = StateGraph(S)
g.add_node("a", a)
g.add_edge(START, "a")
g.add_edge("a", END)
graph = g.compile()
"""


@pytest.mark.parametrize(("flags", "drifted"), [([], [[], []]), (["--live"], [["a"], ["a"]])])
def test_replay_serves_unchanged_nodes_unless_live(
    tmp_path: Path, flags: list[str], drifted: list[list[str]]
) -> None:
    from lgcov.bench_side import side_main

    graph = tmp_path / "graph.py"
    graph.write_text(NOISY, encoding="utf-8")
    runs, out, head = (tmp_path / f for f in ("base.json", "drifted.json", "head.json"))
    assert side_main(["record", f"{graph}:graph", str(_cases(tmp_path)), str(runs)]) == 0
    assert side_main(["replay", f"{graph}:graph", str(runs), str(out), str(head), *flags]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == drifted


@pytest.mark.parametrize("flags", [[], ["--live"]])
def test_bench_passes_live_to_head_s_replay_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: list[str]
) -> None:
    steps: list[tuple[str, ...]] = []

    def side(_b: Benchmark, name: str, _root: Path, *args: str) -> None:
        steps.append((name, *args))

    monkeypatch.setattr("lgcov.cli._run_side", side)
    out = ["--out", str(tmp_path / "b.md"), "--json", str(tmp_path / "r.json")]
    main(["bench", str(LINEAR.parent), "--only", "linear", *flags, *out])
    assert [s[:2] for s in steps] == [("head", "check"), ("base", "record"), ("head", "replay")]
    assert [s for s in steps if "--live" in s] == ([steps[2]] if flags else [])


# T211: --noise-repeats K runs base's code K more times on a node's recorded input, and head's K
# times, so a node is named only when head sits outside base's own spread.
REPEATED = """\
import os
import socket
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

WORDS = ("alpha", "beta", "gamma")


class S(TypedDict, total=False):
    q: str
    r: str


def a(state: S) -> S:
    version = VERSION
    socket.getaddrinfo("localhost", 80)  # a model call, as lgcov's offline probe sees it
    calls = Path(os.environ["LGCOV_TEST_CALLS"])
    n = int(calls.read_text()) if calls.exists() else 0
    calls.write_text(str(n + 1))
    return {"r": f"{state['q']} ANSWER {WORDS[n % 3]}"}  # a live model: a new word each time


g = StateGraph(S)
g.add_node("a", a)
g.add_edge(START, "a")
g.add_edge("a", END)
graph = g.compile()
"""
SHIPS, REFUNDS = "the order ships on friday", "refunds take ten working days sorry"


def _repeated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, head: tuple[str, str], *flags: str
) -> dict[str, Any]:
    monkeypatch.setenv("LGCOV_TEST_CALLS", str(tmp_path / "calls"))
    for side, (version, answer) in (("base", ("1", SHIPS)), ("head", head)):
        text = REPEATED.replace("VERSION", version).replace("ANSWER", answer)
        (tmp_path / f"{side}.py").write_text(text, encoding="utf-8")
    row = "graph: graph.py:graph\nbase_patch: base.py\nhead_patch: head.py\ncases:\n  - {q: hi}\n"
    (tmp_path / "row.yaml").write_text(row + "expected: {root: [], propagated: []}\n", "utf-8")
    out = ["--out", str(tmp_path / "b.md"), "--json", str(tmp_path / "r.json")]
    assert main(["bench", str(tmp_path), *flags, "--noise-repeats", "3", *out]) == 0
    [found] = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["benchmarks"]
    assert isinstance(found, dict)
    return found


@pytest.mark.slow
def test_base_against_base_with_repeats_names_no_root_and_nothing_inconclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = _repeated(tmp_path, monkeypatch, ("1", SHIPS), "--live")
    assert (found["found_root"], found["inconclusive"], found["first_drift"]) == ([], {}, ["a"])
    assert found["found_propagated"] == []  # not "it only inherited the damage" either
    [check] = found["noise"].values()
    assert (check["k"], check["min_head_to_base"]) == (3, 0.0) and check["spread"] > 0
    assert check["verdict"] == "no drift detected above noise (K=3)"


@pytest.mark.slow
def test_a_changed_model_node_outside_base_s_spread_is_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = _repeated(tmp_path, monkeypatch, ("2", REFUNDS))
    assert (found["found_root"], found["inconclusive"]) == (["a"], {})
    assert found["noise"]["a"]["verdict"] == "drift above noise (K=3)"


@pytest.mark.slow
def test_a_changed_model_node_inside_base_s_spread_is_inconclusive_never_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = _repeated(tmp_path, monkeypatch, ("2", SHIPS))
    assert (found["found_root"], found["found_propagated"]) == ([], [])
    assert found["inconclusive"] == {"a": "no drift detected above noise (K=3)"}


def test_without_noise_repeats_nothing_is_repeated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LGCOV_TEST_CALLS", str(tmp_path / "calls"))
    for side in ("base", "head"):
        text = REPEATED.replace("VERSION", "1").replace("ANSWER", SHIPS)
        (tmp_path / f"{side}.py").write_text(text, encoding="utf-8")
    row = "graph: graph.py:graph\nbase_patch: base.py\nhead_patch: head.py\ncases:\n  - {q: hi}\n"
    (tmp_path / "row.yaml").write_text(row + "expected: {root: [], propagated: []}\n", "utf-8")
    out = ["--out", str(tmp_path / "b.md"), "--json", str(tmp_path / "r.json")]
    assert main(["bench", str(tmp_path), "--live", *out]) == 0
    assert (tmp_path / "calls").read_text(encoding="utf-8") == "2"  # one record, one replay
