"""Code fingerprints: what makes `lgcov replay` re-run a node instead of serving it."""

import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from lgcov.fingerprint import fingerprint

DEMO = (Path(__file__).parent / "fixtures" / "demo_graph.py").read_text(encoding="utf-8")


def _module(path: Path, source: str) -> ModuleType:
    """Import `source` from `path` without registering it, as `lgcov replay` loads graphs."""
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_code_it_cannot_vouch_for_has_no_fingerprint(tmp_path: Path) -> None:
    namespace: dict[str, object] = {}
    exec("def node(state):\n    return {}\n", namespace)  # no source file to read
    assert fingerprint(namespace["node"]) is None
    src = "from fixtures.graphs import linear\nINNER = linear()\n\n"
    graph = _module(tmp_path / "g.py", src + "def node(state):\n    return INNER.invoke(state)\n")
    assert fingerprint(graph.node) is None  # a compiled graph it invokes is not followed
    assert fingerprint(len) is None  # not a Python function
