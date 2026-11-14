"""Code fingerprints: what makes `lgcov replay` re-run a node instead of serving it."""

import copy
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import typing
import venv
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from lgcov.cli import _import_file
from lgcov.fingerprint import fingerprint

DEMO = (Path(__file__).parent / "fixtures" / "demo_graph.py").read_text(encoding="utf-8")


def _module(path: Path, source: str) -> ModuleType:
    """Import `source` from `path` as `lgcov replay` loads graphs."""
    path.write_text(source, encoding="utf-8")
    return _import_file(str(path))


def test_same_code_moved_to_another_file_keeps_its_fingerprint(tmp_path: Path) -> None:
    one = _module(tmp_path / "one.py", DEMO)
    two = _module(tmp_path / "two.py", "\n\n" + DEMO)  # other name, other line numbers
    for node in ("classify", "lookup_order", "respond"):
        assert fingerprint(getattr(one, node)) == fingerprint(getattr(two, node)) is not None


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("a concise support", "a friendly support"),  # a module constant respond reads
        ("prompt[-40:]", "prompt[-30:]"),  # the helper respond calls
    ],
)
def test_an_edit_changes_only_the_nodes_that_depend_on_it(
    tmp_path: Path, old: str, new: str
) -> None:
    assert DEMO.replace(old, new) != DEMO
    base = _module(tmp_path / "base.py", DEMO)
    edited = _module(tmp_path / "edited.py", DEMO.replace(old, new))
    assert fingerprint(edited.respond) != fingerprint(base.respond)
    assert fingerprint(edited.classify) == fingerprint(base.classify)
    assert fingerprint(edited.lookup_order) == fingerprint(base.lookup_order)


def test_closure_values_count() -> None:
    def build(prompt: str) -> Callable[[dict[str, str]], dict[str, str]]:
        def answer(state: dict[str, str]) -> dict[str, str]:
            return {"a": f"{prompt}:{state['q']}"}

        return answer

    assert fingerprint(build("v1")) == fingerprint(build("v1"))
    assert fingerprint(build("v1")) != fingerprint(build("v2"))


def test_a_constant_from_another_project_module_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    node = "def respond(state):\n    return {'a': prompts.SYSTEM + state['q']}\n"
    found = []
    for i, system in enumerate(["concise", "concise", "friendly"]):
        (tmp_path / f"lgcov_fp_prompts{i}.py").write_text(f"SYSTEM = {system!r}\n")
        graph = _module(tmp_path / f"graph{i}.py", f"import lgcov_fp_prompts{i} as prompts\n{node}")
        found.append(fingerprint(graph.respond))
    assert found[0] == found[1] != found[2]


def test_a_project_class_counts_by_its_members(tmp_path: Path) -> None:
    src = "class Plan:\n    steps = 3\n\ndef node(state):\n    return {'n': Plan.steps}\n"
    one = _module(tmp_path / "one.py", src)
    two = _module(tmp_path / "two.py", src.replace("steps = 3", "steps = 4"))
    assert fingerprint(one.node) is not None
    assert fingerprint(one.node) != fingerprint(two.node)


# A node that reaches a pydantic class, as open_deep_research's nodes reach `Configuration`.
PYDANTIC = """\
from enum import Enum
from pydantic import BaseModel, Field

class Color(Enum):
    RED = "red"

class Inner(BaseModel):
    size: int = 1

class Config(BaseModel):
    color: Color = Field(default=Color.RED)
    name: str = Field(default="old")
    inner: Inner | None = None

def node(state):
    return {"a": Config().name}
"""

# The same, with what keeps a class's id outside a plain ref: each kind of decorator's info, on the
# class, a parent and a class it holds, and the ref of a parametrized generic.
DECORATED = """\
from typing import Generic, TypeVar
from pydantic import (
    BaseModel,
    computed_field,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

T = TypeVar("T")

class Box(BaseModel, Generic[T]):
    item: T

class Parent(BaseModel):
    size: int = 1

    @field_validator("size", mode="after")
    @classmethod
    def positive(cls, value):
        return abs(value)

class Config(Parent):
    box: Box[int] | None = None
    name: str = "old"

    @model_validator(mode="after")
    def named(self):
        return self

    @field_serializer("name", when_used="always")
    def spoken(self, value):
        return value.strip()

    @computed_field
    @property
    def label(self) -> str:
        return self.name.title()

class Whole(BaseModel):
    config: Config = Config()

    @model_serializer(mode="plain", when_used="always")
    def dumped(self):
        return {"config": self.config.name.lower()}

def node(state):
    return {"a": Whole().config.name}
"""

USES_HELPER = "from {name} import helper\n\ndef node(state):\n    return {{'a': helper()}}\n"

# Classes that build themselves at first use. Until then, their schema hides what they hold.
NOT_BUILT = {
    "later_class": """\
from __future__ import annotations
from pydantic import BaseModel

class Plan(BaseModel):
    limits: Limits

class Limits(BaseModel):
    steps: int = 3

def node(state):
    return {"a": Plan(limits={}).limits.steps}
""",
    "defer_build": """\
from pydantic import BaseModel, ConfigDict

class Limits(BaseModel):
    steps: int = 3

class Plan(BaseModel):
    model_config = ConfigDict(defer_build=True)
    limits: Limits | None = None

def node(state):
    return {"a": Plan(limits={}).limits.steps}
""",
    "dataclass": """\
from pydantic import BaseModel
from pydantic.dataclasses import dataclass

@dataclass
class Plan:
    limits: "Limits"

class Limits(BaseModel):
    steps: int = 3

def node(state):
    return {"a": Plan(limits={}).limits.steps}
""",
}

FINGERPRINT_A_FILE = """\
import importlib.util, sys
from lgcov.fingerprint import fingerprint
spec = importlib.util.spec_from_file_location("graph", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(fingerprint(module.node))
"""

# T206: one graph per cause, found on 7 Nov, of a fingerprint that changed in every process, so the
# node was never served. Each node reaches a project pydantic class that uses only that.
CAUSES = {
    "validator": """\
from pydantic import BaseModel, field_validator, model_validator

class Parent(BaseModel):
    size: int = 1

    @field_validator("size", mode="after")
    @classmethod
    def positive(cls, value):
        return abs(value)

class Config(Parent):
    @model_validator(mode="after")
    def checked(self):
        return self

def node(state):
    return {"a": str(Config(size=state["n"]).size)}
""",
    "serializer": """\
from pydantic import BaseModel, field_serializer, model_serializer

class Inner(BaseModel):
    name: str = " old "

    @field_serializer("name", when_used="always")
    def spoken(self, value):
        return value.strip()

class Config(BaseModel):
    inner: Inner = Inner()

    @model_serializer(mode="plain")
    def dumped(self):
        return {"inner": self.inner.model_dump()}

def node(state):
    return {"a": f"{Config().model_dump()} {state['n']}"}
""",
    "computed_field": """\
from pydantic import BaseModel, computed_field

class Config(BaseModel):
    name: str = "old"

    @computed_field
    @property
    def label(self) -> str:
        return self.name.title()

def node(state):
    return {"a": f"{Config().label} {state['n']}"}
""",
    "generic": """\
from typing import Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T")

class Box(BaseModel, Generic[T]):
    item: T

class Config(BaseModel):
    box: Box[int] | None = None

def node(state):
    return {"a": str(Config(box={"item": state["n"]}).box.item)}
""",
    # The first case's run fills pydantic's setattr cache on the class. A served first case fills
    # nothing, so the second case's recorded fingerprint was never matched.
    "attribute_assignment": """\
from pydantic import BaseModel

class Config(BaseModel):
    name: str = "old"

def node(state):
    config = Config()
    config.name = f"new {state['n']}"
    return {"a": config.name}
""",
}

# A node reaching a LangChain message, whose validators and serializers made it run live wherever
# installed code counted as project code.
MESSAGE = """\
from langchain_core.messages import AIMessage

def node(state):
    return {"a": AIMessage(content=f"hi {state['n']}").content}
"""

# What turns a node's file into a graph `lgcov replay` can load as `graph.py:graph`.
AS_GRAPH = """
from typing import TypedDict
from langgraph.graph import END, START, StateGraph

class State(TypedDict, total=False):
    n: int
    a: str

builder = StateGraph(State)
builder.add_node("node", node)
builder.add_edge(START, "node")
builder.add_edge("node", END)
graph = builder.compile()
"""

# Run in a fresh process: record two cases from one load of the graph, as `lgcov record` does with
# its inputs, or serve them as `lgcov replay` does. Prints each step's fingerprint and, when it
# serves, how each call ran.
RECORD_OR_SERVE = """\
import json, sys
from pathlib import Path
from lgcov.cassette import record, replay_run, runs_from_json, runs_to_json
from lgcov.cli import _load_graph

mode, graph, saved = sys.argv[1], _load_graph(sys.argv[2] + ":graph"), Path(sys.argv[3])
if mode == "record":
    runs = [record(graph, {"n": n}) for n in (1, 2)]
    saved.write_text(runs_to_json(runs))
    hows = []
else:
    runs = [replay_run(graph, c, serve=True) for c in runs_from_json(saved.read_text())]
    hows = [c.how for run in runs for c in run.calls]
print(json.dumps({"codes": [s.code for run in runs for s in run.steps], "hows": hows}))
"""


@pytest.mark.parametrize("source", [PYDANTIC, DECORATED], ids=["fields", "decorators"])
def test_a_pydantic_class_keeps_its_fingerprint_from_another_checkout(
    tmp_path: Path, source: str
) -> None:
    (tmp_path / "base").mkdir()
    (tmp_path / "head").mkdir()
    base = _module(tmp_path / "base" / "graph.py", source)
    head = _module(tmp_path / "head" / "graph.py", source)
    assert fingerprint(base.node) == fingerprint(head.node) is not None


@pytest.mark.slow
@pytest.mark.parametrize("source", [PYDANTIC, DECORATED], ids=["fields", "decorators"])
def test_a_pydantic_class_keeps_its_fingerprint_in_another_process(
    tmp_path: Path, source: str
) -> None:
    path = tmp_path / "graph.py"
    path.write_text(source, encoding="utf-8")
    found = {
        subprocess.run(
            [sys.executable, "-c", FINGERPRINT_A_FILE, str(path)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for _ in range(2)
    }
    assert len(found) == 1
    assert found != {"None"}


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('default="old"', 'default="new"'),  # a field's default, as a config change is
        ("size: int = 1", "size: int = 2"),  # a nested model's default
        ('RED = "red"', 'RED = "crimson"'),  # an enum a field holds
    ],
)
def test_an_edit_to_a_pydantic_class_counts(tmp_path: Path, old: str, new: str) -> None:
    assert PYDANTIC.replace(old, new) != PYDANTIC
    (tmp_path / "base").mkdir()
    (tmp_path / "head").mkdir()
    base = _module(tmp_path / "base" / "graph.py", PYDANTIC)
    head = _module(tmp_path / "head" / "graph.py", PYDANTIC.replace(old, new))
    assert fingerprint(base.node) is not None
    assert fingerprint(base.node) != fingerprint(head.node)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("abs(value)", "-abs(value)"),  # a field validator on a parent class
        ('"size", mode="after"', '"size", mode="before"'),  # when it runs
        ("return self\n", "assert self.name\n        return self\n"),  # a model validator
        ("value.strip()", "value.lower()"),  # a field serializer
        ('"name", when_used="always"', '"name", when_used="json"'),  # when it runs
        ("name.title()", "name.upper()"),  # a computed field
        ("name.lower()", "name.casefold()"),  # a model serializer
        ("Box[int]", "Box[str]"),  # a generic's type argument
    ],
)
def test_an_edit_to_a_decorated_pydantic_class_counts(tmp_path: Path, old: str, new: str) -> None:
    assert DECORATED.replace(old, new) != DECORATED
    (tmp_path / "base").mkdir()
    (tmp_path / "head").mkdir()
    base = _module(tmp_path / "base" / "graph.py", DECORATED)
    head = _module(tmp_path / "head" / "graph.py", DECORATED.replace(old, new))
    assert fingerprint(base.node) is not None
    assert fingerprint(base.node) != fingerprint(head.node)


def test_setting_an_attribute_keeps_a_pydantic_class_s_fingerprint(tmp_path: Path) -> None:
    # pydantic caches a handler on the class the first time a run sets that attribute. A served
    # node sets nothing, so the next case would otherwise miss the step recorded for it.
    graph = _module(tmp_path / "graph.py", PYDANTIC)
    before = fingerprint(graph.node)
    graph.Config().name = "new"
    assert fingerprint(graph.node) == before is not None


def test_copying_an_instance_keeps_its_class_s_fingerprint(tmp_path: Path) -> None:
    # copy and pickle leave `__slotnames__` on the class, and lgcov deep-copies state itself.
    src = "from dataclasses import dataclass\n\n@dataclass\nclass Plan:\n    steps: int = 3\n\n"
    graph = _module(tmp_path / "graph.py", src + "def node(state):\n    return {'a': Plan}\n")
    before = fingerprint(graph.node)
    copy.deepcopy(graph.Plan())
    assert fingerprint(graph.node) == before is not None


@pytest.mark.parametrize("source", NOT_BUILT.values(), ids=NOT_BUILT.keys())
def test_an_edit_behind_a_class_not_built_yet_counts(tmp_path: Path, source: str) -> None:
    # Unseen, it would serve the old output: a PR's regression would pass.
    found = []
    for i, text in enumerate([source, source, source.replace("steps: int = 3", "steps: int = 9")]):
        (tmp_path / str(i)).mkdir()
        found.append(fingerprint(_module(tmp_path / str(i) / "graph.py", text).node))
    assert found[0] is not None
    assert found[0] == found[1] != found[2]


@pytest.mark.parametrize("source", NOT_BUILT.values(), ids=NOT_BUILT.keys())
def test_first_use_keeps_the_fingerprint_of_a_class_not_built_yet(
    tmp_path: Path, source: str
) -> None:
    graph = _module(tmp_path / "graph.py", source)
    before = fingerprint(graph.node)
    assert graph.node({}) == {"a": 3}
    assert fingerprint(graph.node) == before is not None


@pytest.mark.parametrize(
    "source",
    [
        "from typing import TypedDict\n\nclass Plan(TypedDict):\n    steps: int\n\n",
        "from typing import NamedTuple\n\nclass Plan(NamedTuple):\n    steps: int = 3\n\n",
    ],
    ids=["typeddict", "namedtuple"],
)
def test_a_field_s_type_counts_before_anything_reads_it(tmp_path: Path, source: str) -> None:
    # Python 3.14 computes annotations at first read and keeps them on the class, and these
    # classes leave the computing to `typing`, whose code counts by name.
    node = "def node(state):\n    return {'a': Plan}\n"
    (tmp_path / "base").mkdir()
    (tmp_path / "head").mkdir()
    base = _module(tmp_path / "base" / "graph.py", source + node)
    head = _module(
        tmp_path / "head" / "graph.py", source.replace("steps: int", "steps: str") + node
    )
    before = fingerprint(base.node)
    assert before is not None
    assert before != fingerprint(head.node)
    typing.get_type_hints(base.Plan)
    assert fingerprint(base.node) == before


@pytest.mark.skipif(sys.version_info < (3, 14), reason="earlier Pythons read annotations at once")
def test_a_name_only_a_type_checker_sees_still_gets_a_fingerprint(tmp_path: Path) -> None:
    # `if TYPE_CHECKING: from x import Missing`, then an annotation with no quotes.
    src = "class Plan:\n    steps: Missing\n\n"
    graph = _module(tmp_path / "graph.py", src + "def node(state):\n    return {'a': Plan}\n")
    assert fingerprint(graph.node) is not None


@pytest.mark.parametrize("source", [PYDANTIC, DECORATED], ids=["fields", "decorators"])
def test_a_class_gets_a_fingerprint_when_pydantic_itself_counts_as_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    # As under `pip install --target`: the walk reaches `BaseModel`, which isn't a model to build.
    graph = _module(tmp_path / "graph.py", source)
    monkeypatch.setattr("lgcov.fingerprint._SITE_DIRS", frozenset())
    monkeypatch.setattr("lgcov.fingerprint._LIBRARY", (Path(sys.base_prefix).resolve(),))
    assert fingerprint(graph.node) is not None


def test_a_name_pydantic_cannot_resolve_still_gets_a_fingerprint(tmp_path: Path) -> None:
    src = "from pydantic import BaseModel\n\nclass Plan(BaseModel):\n    limits: 'Missing'\n\n"
    graph = _module(tmp_path / "graph.py", src + "def node(state):\n    return {'a': Plan}\n")
    assert fingerprint(graph.node) is not None


@pytest.mark.parametrize("folder", ["site-packages", "dist-packages"])
def test_code_installed_outside_this_environment_counts_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, folder: str
) -> None:
    # `uv run --with` puts a new environment in front of the project's: `sys.prefix` is the new
    # one, and what's installed in the other is still library code. An upgrade is no edit.
    site = tmp_path / "env" / "lib" / "python3.12" / folder
    site.mkdir(parents=True)
    monkeypatch.syspath_prepend(str(site))
    name = f"lgcov_fp_{folder.replace('-', '_')}"
    library = site / f"{name}.py"
    library.write_text("def helper():\n    return 1\n", encoding="utf-8")
    graph = _module(tmp_path / "graph.py", USES_HELPER.format(name=name))
    before = fingerprint(graph.node)
    library.write_text("def helper():\n    return 2\n", encoding="utf-8")
    assert fingerprint(graph.node) == before is not None


def test_a_project_file_linked_into_site_packages_still_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `flit install --symlink` leaves the code in the project, where an edit has to count.
    site = tmp_path / "env" / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    monkeypatch.syspath_prepend(str(site))
    source = tmp_path / "lgcov_fp_linked.py"
    source.write_text("def helper():\n    return 1\n", encoding="utf-8")
    (site / source.name).symlink_to(source)
    graph = _module(tmp_path / "graph.py", USES_HELPER.format(name=source.stem))
    before = fingerprint(graph.node)
    assert before is not None
    source.write_text("def helper():\n    return 2\n", encoding="utf-8")
    assert fingerprint(graph.node) != before


class _Found(typing.TypedDict):
    codes: list[str | None]  # each step's fingerprint, in order
    hows: list[str]  # how each call ran, when served


def _record_then_serve(
    graph: Path, python: str = sys.executable, env: dict[str, str] | None = None
) -> tuple[_Found, _Found]:
    """Record two cases in one fresh process, then serve them in another."""
    saved = graph.with_name("runs.json")
    found: list[_Found] = []
    for mode in ("record", "serve"):
        args = [python, "-c", RECORD_OR_SERVE, mode, str(graph), str(saved)]
        done = subprocess.run(args, capture_output=True, text=True, env=env)
        assert done.returncode == 0, done.stderr
        found.append(json.loads(done.stdout))
    return found[0], found[1]


@pytest.mark.slow
@pytest.mark.parametrize("cause", CAUSES)
def test_an_unchanged_node_is_served_from_a_fresh_process(tmp_path: Path, cause: str) -> None:
    graph = tmp_path / "graph.py"
    graph.write_text(CAUSES[cause] + AS_GRAPH, encoding="utf-8")
    recorded, served = _record_then_serve(graph)
    assert None not in recorded["codes"]
    assert served["codes"] == recorded["codes"]  # what a fresh process makes of the same code
    assert served["hows"] == ["served", "served"]


def _uv_run_with(tmp_path: Path) -> tuple[str, dict[str, str]]:
    # `uv run --with` puts a new environment in front of the project's, and the .pth file it writes
    # there adds the project's site-packages: installed code is outside `sys.prefix`.
    root = tmp_path / "overlay"
    venv.create(root, symlinks=True)
    site = sysconfig.get_path("purelib", "venv", vars={"base": str(root), "platbase": str(root)})
    project = sysconfig.get_path("purelib")
    Path(site, "_uv_ephemeral_overlay.pth").write_text(
        f"import site; site.addsitedir({project!r})\n", encoding="utf-8"
    )
    return str(root / "bin" / "python"), dict(os.environ)


def _uv_link_mode_symlink(tmp_path: Path) -> tuple[str, dict[str, str]]:
    # UV_LINK_MODE=symlink links each installed file to uv's cache, so a file's resolved path is in
    # no site-packages folder and under no prefix. Copies outside both stand in for the cache.
    cache, linked = tmp_path / "cache", tmp_path / "linked"
    for name in ("langchain_core", "pydantic", "pydantic_core"):
        installed = Path(sysconfig.get_path("purelib"), name)
        shutil.copytree(installed, cache / name, ignore=shutil.ignore_patterns("__pycache__"))
    for file in cache.rglob("*"):
        if file.is_file():
            link = linked / file.relative_to(cache)
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(file)
    path = os.pathsep.join(filter(None, [str(linked), os.environ.get("PYTHONPATH")]))
    return sys.executable, {**os.environ, "PYTHONPATH": path}


@pytest.mark.slow
@pytest.mark.parametrize(
    "layout", [_uv_run_with, _uv_link_mode_symlink], ids=["uv_run_with", "uv_link_mode_symlink"]
)
def test_a_node_reaching_a_message_is_served_in_a_layered_install(
    tmp_path: Path, layout: Callable[[Path], tuple[str, dict[str, str]]]
) -> None:
    graph = tmp_path / "graph.py"
    graph.write_text(MESSAGE + AS_GRAPH, encoding="utf-8")
    recorded, served = _record_then_serve(graph, *layout(tmp_path))
    assert None not in recorded["codes"]
    assert served["codes"] == recorded["codes"]
    assert served["hows"] == ["served", "served"]


def test_code_it_cannot_vouch_for_has_no_fingerprint(tmp_path: Path) -> None:
    namespace: dict[str, object] = {}
    exec("def node(state):\n    return {}\n", namespace)  # no source file to read
    assert fingerprint(namespace["node"]) is None
    src = "from fixtures.graphs import linear\nINNER = linear()\n\n"
    graph = _module(tmp_path / "g.py", src + "def node(state):\n    return INNER.invoke(state)\n")
    assert fingerprint(graph.node) is None  # a compiled graph it invokes is not followed
    assert fingerprint(len) is None  # not a Python function


# T212: a function is hashed from its syntax tree, so comments and formatting don't count.
PINNED = """\
import inspect

LIMIT = 3


def helper(x):
    return x + LIMIT


def node(state):
    total = helper(state["x"])
    return {"x": [total, "a"], "ok": total > 0}


async def anode(state, *, flag=False):
    return {"x": [i async for i in state["x"] if i] if flag else None}


def looks(state):
    return {"x": f"{state['x']!r:>{LIMIT}}", "n": len(inspect.signature(looks).parameters)}
"""
REFORMATTED = """\
import inspect

LIMIT = 3  # how many


def helper(x):
    # a comment
    return (x + LIMIT)


def node(state):  # the node

    total = helper(
        state['x'],
    )
    return {
        'x': [total, 'a'],  # quotes, wrapping and a trailing comma
        "ok": (total > 0),
    }
"""


def test_comments_and_formatting_keep_a_fingerprint(tmp_path: Path) -> None:
    one = _module(tmp_path / "one.py", PINNED)
    two = _module(tmp_path / "two.py", REFORMATTED)
    assert fingerprint(one.node) == fingerprint(two.node) is not None
    edited = _module(tmp_path / "three.py", REFORMATTED.replace("x + LIMIT", "x - LIMIT"))
    assert fingerprint(edited.node) != fingerprint(one.node)  # a helper's body still counts


def test_fingerprints_are_the_same_on_every_python(tmp_path: Path) -> None:
    # Literal values: CI runs the newest Python, and a cassette recorded on one must serve on
    # another. A new Python that changes one fails here before it reaches a baseline.
    pinned = _module(tmp_path / "pinned.py", PINNED)
    found = {n: fingerprint(getattr(pinned, n)) for n in ("node", "anode", "looks")}
    assert found == {
        "node": "1f1ec544dc4c45c4",
        "anode": "091cc1cf1e0158aa",
        "looks": "d5efa1d120d3940e",
    }
    # The old scheme, from the function's raw lines: what recordings made before T212 hold.
    old = {n: fingerprint(getattr(pinned, n), raw=True) for n in ("node", "anode", "looks")}
    assert old == {
        "node": "4516f98c0333a7a2",
        "anode": "a027a7447681e77d",
        "looks": "22ac0d31f3ed7b22",
    }


# Two functions that differ must never share a fingerprint: that would serve a stale output.
_DISTINCT = [
    ["return 0", "return None", "return False", "return ''", "return 0.0", "return", "pass"],
    ["x = 0", "x = None", "x = False", "x = []", "x = ()", "x = {}", "x = ''"],
    ["return 1", "return 1.0", "return True", "return '1'", "return 1j", "return -1"],
    ["return b'a'", "return 'a'", "return ...", "return a"],
    ["yield", "yield None", "yield 0", "yield from ()"],
    ["return []", "return ()", "return [[]]", "return [()]", "return {}", "return {**a}"],
    ["return f()", "return f(*[])", "return f(**{})", "return f(None)", "return f(0)"],
    [
        "try:\n        f()\n    except:\n        pass",
        "try:\n        f()\n    except Exception:\n        pass",
        "try:\n        f()\n    except Exception as e:\n        pass",
        "try:\n        f()\n    finally:\n        pass",
    ],
    ["import a", "from . import a", "from .. import a", "from a import a", "import a as a"],
    ["x: int = 0", "x: int", "(x): int = 0", "x = 0", "x += 0"],
    ["return f'{a}'", "return f'{a!r}'", "return f'{a!s}'", "return f'{a:0}'", "return a"],
    ["return [i for i in a]", "return [i async for i in a]", "return (i for i in a)"],
    ["return a[0]", "return a[:0]", "return a[0:]", "return a[::0]", "return a[:]", "return a"],
    ["return lambda: 0", "return lambda: None", "return lambda a=0: a", "return lambda a: a"],
]


@pytest.mark.parametrize("bodies", _DISTINCT)
def test_bodies_that_differ_get_different_fingerprints(tmp_path: Path, bodies: list[str]) -> None:
    found = []
    for i, body in enumerate(bodies):
        kind = "async def" if "async for" in body else "def"
        source = f"{kind} node(state, a=1, f=len):\n    {body}\n"
        found.append(fingerprint(_module(tmp_path / f"m{i}.py", source).node))
    assert None not in found and len(set(found)) == len(bodies)


def test_a_function_that_reads_source_is_hashed_from_its_raw_lines(tmp_path: Path) -> None:
    source = "import inspect\n\n\ndef node(state):\n    return inspect.getsource(node)\n"
    one = _module(tmp_path / "one.py", source)
    two = _module(tmp_path / "two.py", source.replace("return", "# a comment\n    return"))
    assert fingerprint(one.node) != fingerprint(two.node)
    assert fingerprint(one.node) == fingerprint(one.node, raw=True)


# T212: a node function's own docstring doesn't count, where nothing can read it.
DOCS = '''\
import inspect


def helper(x):
    """Add one."""
    return x + 1


def node(state):
    """Summarise the order."""
    return {"x": helper(state["x"])}


async def anode(state):
    """Summarise the order."""
    return {"x": state["x"]}



def keep(fn):
    return fn


@keep
def decorated(state):
    """Summarise the order."""
    return {"x": state["x"]}


def reads(state):
    """Summarise the order."""
    return {"x": reads.__doc__}


def asks(state):
    """Summarise the order."""
    return {"x": inspect.getdoc(asks)}


def described(fn):
    return fn.__doc__


def passes(state):
    """Summarise the order."""
    return {"x": described(passes)}


def one_line(state): """Summarise the order."""; return {"x": 1}


def nested(state):
    def inner():
        """Summarise the order."""
        return 1

    class Box:
        """Summarise the order."""

    return {"x": inner(), "b": Box.__name__}
'''
_REWORDED = DOCS.replace("Summarise the order.", "Sum the order up.\n\n    In two lines.")


def test_a_node_s_own_docstring_does_not_count(tmp_path: Path) -> None:
    one, two = _module(tmp_path / "one.py", DOCS), _module(tmp_path / "two.py", _REWORDED)
    for name in ("node", "anode"):
        assert fingerprint(getattr(one, name), node=True) is not None
        assert fingerprint(getattr(one, name), node=True) == fingerprint(
            getattr(two, name), node=True
        )
    # Writing one, or deleting it, is the same kind of edit.
    gone = _module(tmp_path / "gone.py", DOCS.replace('    """Summarise the order."""\n', "", 1))
    assert fingerprint(gone.node, node=True) == fingerprint(one.node, node=True)
    body = _module(tmp_path / "three.py", _REWORDED.replace('helper(state["x"])', "0"))
    assert fingerprint(body.node, node=True) != fingerprint(one.node, node=True)


def test_a_docstring_counts_for_any_function_that_is_not_a_node(tmp_path: Path) -> None:
    # A router, or any caller that doesn't say `node`: as before.
    one, two = _module(tmp_path / "one.py", DOCS), _module(tmp_path / "two.py", _REWORDED)
    assert fingerprint(one.node) != fingerprint(two.node)


@pytest.mark.parametrize(
    ("name", "old"),
    [
        ("node", "Add one."),  # a helper's docstring: the helper may be a tool somewhere
        ("decorated", "Summarise the order."),  # a decorator can read fn.__doc__
        ("reads", "Summarise the order."),
        ("asks", "Summarise the order."),
        ("passes", "Summarise the order."),  # a helper reads it
        ("one_line", "Summarise the order."),  # shares its line with code
        ("nested", "Summarise the order."),  # a nested function's and a class's
    ],
)
def test_a_docstring_something_could_read_still_counts(tmp_path: Path, name: str, old: str) -> None:
    assert old in DOCS
    one = _module(tmp_path / "one.py", DOCS)
    two = _module(tmp_path / "two.py", DOCS.replace(old, "Something else."))
    assert fingerprint(getattr(one, name), node=True) != fingerprint(getattr(two, name), node=True)


def test_node_fingerprints_are_the_same_on_every_python(tmp_path: Path) -> None:
    docs = _module(tmp_path / "docs.py", DOCS)
    found = {n: fingerprint(getattr(docs, n), node=True) for n in ("node", "anode", "reads")}
    assert found == {
        "node": "964711741fd433b7",
        "anode": "6aa9cccf9c92df3e",
        "reads": "47aff0937771fd56",
    }
