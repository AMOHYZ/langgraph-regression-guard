"""T133 PR A: `Unsupported(reason)` for attribution that isn't safe (cases 1–3)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from fixtures.replay_audit import loop

from lgcov.attribution import (
    GRAPH_NOT_GIVEN,
    UNEXPLAINED,
    NodeLabels,
    Probe,
    classify,
    explain_runs,
)
from lgcov.cassette import Cassette, accept_nodes, record, replay_run
from lgcov.ci_report import CiResult, Tally, render_ci_report
from lgcov.cli import _run_root
from lgcov.unsupported import (
    channel_fingerprints,
    guard_labels,
    visit_counts,
)
from lgcov.verdict_actions import Evidence, decide_action

NO_DECISION = "⚠️ No decision: needs_human"


def _load(name: str, src: str) -> Any:
    path = Path("/tmp") / f"{name}.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _reducer_graph(name: str, reducer: str) -> Any:
    return _load(
        name,
        f"""
from __future__ import annotations
import operator
from typing import Annotated, TypedDict
from langgraph.graph import END, START, StateGraph

def replace(left: object, right: object) -> object:
    return right

class State(TypedDict):
    vals: Annotated[list[int], {reducer}]
    n: int

def a(s: State) -> dict[str, object]:
    return {{"vals": [1]}}

def b(s: State) -> dict[str, object]:
    return {{"vals": [10]}}

def c(s: State) -> dict[str, object]:
    return {{"n": sum(s["vals"])}}

def build() -> object:
    g = StateGraph(State)
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_node("c", c)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    g.add_edge("c", END)
    return g.compile()
""",
    ).build()


# --- Plumbing: Unsupported → needs_human (existing verdict table, no new exit code) ---


def test_unsupported_evidence_is_needs_human_exit_0() -> None:
    action = decide_action(
        Evidence(
            unsupported=("occurrence pairing is unsafe: node 'worker' ran 2x, baseline has 3x",)
        )
    )
    assert action.state == "unsupported"
    assert action.verdict == "needs_human"
    assert action.exit_code == 0
    assert action.headline == NO_DECISION
    assert "occurrence pairing is unsafe" in action.next_step


def test_unsupported_ci_comment_prints_the_reason() -> None:
    reason = "state schema or reducer changed (channel vals)"
    result = CiResult(
        "root-drift",
        unsupported=(reason,),
        tally=Tally(2, 0, ()),
        replay_lines=("### Cassette replay", "", "No cassette drift (2 runs)."),
    )
    assert result.action.state == "unsupported"
    body = render_ci_report(result)
    assert reason in body
    assert "needs_human" in body
    assert result.action.exit_code == 0


def test_unsupported_does_not_block_under_fail_on_inconclusive() -> None:
    action = decide_action(Evidence(unsupported=("looks like a rename",), inconclusive_blocks=True))
    assert (action.state, action.exit_code, action.verdict) == ("unsupported", 0, "needs_human")


# --- Case 1: occurrence pairing ---


def test_mismatched_visit_counts_currently_mis_label_before_guard() -> None:
    """Failing example: visit counts differ, yet classify still emits a confident label.
    accept_nodes refuses to pair those visits — pairing is unsafe."""
    spy = Mock()
    base = Cassette.from_json(record(loop.build(("t1", "t2", "t3"), spy), {}).to_json())
    spy.reset_mock()
    fresh = replay_run(
        loop.build(("t1", "t2"), spy), Cassette(inputs={}, steps=list(base.steps)), serve=True
    )
    fresh_c = Cassette(inputs={}, steps=list(fresh.steps))
    with pytest.raises(ValueError, match="ran 2x, baseline has 3x"):
        accept_nodes(base, fresh_c, {"worker", "plan", "check"})
    counts = (visit_counts(base.steps), visit_counts(fresh.steps))
    assert counts[0]["worker"] != counts[1]["worker"]
    codes = (
        {s.node: s.code for s in base.steps},
        {s.node: s.code for s in fresh.steps},
    )
    drifted = [d.node for d in fresh.drifts]
    probes = {n: Probe(codes[0].get(n) != codes[1].get(n)) for n in drifted}
    labels = classify(
        drifted, codes, probes, graphs=({"plan", "worker", "check"}, {"plan", "worker", "check"})
    )
    assert labels.root or labels.propagated or labels.inconclusive


def test_mismatched_visit_counts_become_unsupported() -> None:
    spy = Mock()
    base = Cassette.from_json(record(loop.build(("t1", "t2", "t3"), spy), {}).to_json())
    spy.reset_mock()
    fresh = replay_run(
        loop.build(("t1", "t2"), spy), Cassette(inputs={}, steps=list(base.steps)), serve=True
    )
    codes = (
        {s.node: s.code for s in base.steps},
        {s.node: s.code for s in fresh.steps},
    )
    drifted = [d.node for d in fresh.drifts]
    probes = {n: Probe(codes[0].get(n) != codes[1].get(n)) for n in drifted}
    labels = classify(
        drifted, codes, probes, graphs=({"plan", "worker", "check"}, {"plan", "worker", "check"})
    )
    labels = explain_runs(
        labels,
        [_run_root(base, list(fresh.drifts))],
        [drifted],
        ran=[[s.node for s in fresh.steps]],
    )
    guarded, bad = guard_labels(
        labels,
        graphs=({"plan", "worker", "check"}, {"plan", "worker", "check"}),
        counts=(visit_counts(base.steps), visit_counts(fresh.steps)),
    )
    assert bad is not None
    assert bad.pattern == "occurrence"
    assert "ran" in bad.reason and "baseline has" in bad.reason
    assert guarded == NodeLabels((), (), {})


def test_matched_visit_counts_keep_a_correct_root_label() -> None:
    """Matrix: same visit counts, changed middle visit — label stays (supported)."""
    spy = Mock()
    base = Cassette.from_json(record(loop.build(("t1", "t2", "t3"), spy), {}).to_json())
    spy.reset_mock()
    fresh = replay_run(
        loop.build(("t1", "T2", "t3"), spy), Cassette(inputs={}, steps=list(base.steps)), serve=True
    )
    codes = (
        {s.node: s.code for s in base.steps},
        {s.node: s.code for s in fresh.steps},
    )
    drifted = [d.node for d in fresh.drifts]
    probes = {n: Probe(codes[0].get(n) != codes[1].get(n)) for n in drifted}
    labels = classify(
        drifted, codes, probes, graphs=({"plan", "worker", "check"}, {"plan", "worker", "check"})
    )
    labels = explain_runs(
        labels,
        [_run_root(base, list(fresh.drifts))],
        [drifted],
        ran=[[s.node for s in fresh.steps]],
    )
    guarded, bad = guard_labels(
        labels,
        graphs=({"plan", "worker", "check"}, {"plan", "worker", "check"}),
        counts=(visit_counts(base.steps), visit_counts(fresh.steps)),
    )
    assert bad is None
    assert "plan" in guarded.root
    assert guarded.propagated or guarded.root


# --- Case 2: add/delete vs rename / missing graph ---


def test_clear_add_with_trusted_graphs_stays_root() -> None:
    labels = classify(["new"], ({}, {"new": "1"}), {}, graphs=({"a"}, {"a", "new"}))
    guarded, bad = guard_labels(labels, graphs=({"a"}, {"a", "new"}))
    assert bad is None
    assert guarded.root == ("new",)


def test_clear_delete_with_trusted_graphs_stays_root() -> None:
    labels = classify(["old"], ({"old": "1"}, {}), {}, graphs=({"old", "a"}, {"a"}))
    guarded, bad = guard_labels(labels, graphs=({"old", "a"}, {"a"}))
    assert bad is None
    assert guarded.root == ("old",)


def test_missing_graph_shape_is_unsupported_not_inconclusive() -> None:
    labels = classify(["new"], ({}, {"new": "1"}), {}, graphs=(None, {"new"}))
    assert labels.inconclusive == {"new": GRAPH_NOT_GIVEN["base"]}
    guarded, bad = guard_labels(labels, graphs=(None, {"new"}))
    assert bad is not None
    assert bad.pattern == "graph_shape"
    assert "missing" in bad.reason or "untrusted" in bad.reason
    assert guarded == NodeLabels((), (), {})


def test_missing_graph_keeps_other_roots() -> None:
    labels = NodeLabels(
        root=("classify",),
        propagated=("respond",),
        inconclusive={"audit": GRAPH_NOT_GIVEN["base"]},
    )
    guarded, bad = guard_labels(labels, graphs=(None, {"audit", "classify", "respond"}))
    assert bad is not None and bad.pattern == "graph_shape"
    assert guarded.root == ("classify",)
    assert guarded.propagated == ("respond",)
    assert guarded.inconclusive == {}


def test_rename_like_add_and_delete_is_unsupported() -> None:
    labels = classify(
        ["old", "new"],
        ({"old": "1"}, {"new": "2"}),
        {},
        graphs=({"old", "a"}, {"new", "a"}),
    )
    assert set(labels.root) == {"old", "new"}
    guarded, bad = guard_labels(labels, graphs=({"old", "a"}, {"new", "a"}))
    assert bad is not None
    assert bad.pattern == "rename"
    assert "rename" in bad.reason
    assert guarded == NodeLabels((), (), {})


# --- Case 3: reducer / state schema ---


def test_reducer_change_is_mislabelled_before_guard() -> None:
    """Failing example: reducer change → UNEXPLAINED; fingerprints unchanged."""
    base_g = _reducer_graph("t133_add", "operator.add")
    head_g = _reducer_graph("t133_rep", "replace")
    base = Cassette.from_json(record(base_g, {}).to_json())
    fresh = replay_run(head_g, base, serve=True)
    assert [d.node for d in fresh.drifts] == ["c"]
    codes = (
        {s.node: s.code for s in base.steps},
        {s.node: s.code for s in fresh.steps},
    )
    assert codes[0] == codes[1]
    labels = classify(["c"], codes, {"c": Probe(False)}, graphs=({"a", "b", "c"}, {"a", "b", "c"}))
    labels = explain_runs(labels, ["c"], [["c"]], ran=[["a", "b", "c"]])
    assert labels.inconclusive.get("c") == UNEXPLAINED or labels.propagated == ("c",)


def test_reducer_change_becomes_unsupported() -> None:
    base_g = _reducer_graph("t133_add2", "operator.add")
    head_g = _reducer_graph("t133_rep2", "replace")
    base = Cassette.from_json(record(base_g, {}).to_json())
    fresh = replay_run(head_g, base, serve=True)
    codes = (
        {s.node: s.code for s in base.steps},
        {s.node: s.code for s in fresh.steps},
    )
    labels = classify(["c"], codes, {"c": Probe(False)}, graphs=({"a", "b", "c"}, {"a", "b", "c"}))
    labels = explain_runs(labels, ["c"], [["c"]], ran=[["a", "b", "c"]])
    guarded, bad = guard_labels(
        labels,
        graphs=({"a", "b", "c"}, {"a", "b", "c"}),
        counts=(visit_counts(base.steps), visit_counts(fresh.steps)),
        channels=(channel_fingerprints(base_g), channel_fingerprints(head_g)),
    )
    assert bad is not None
    assert bad.pattern == "reducer"
    assert "reducer" in bad.reason or "schema" in bad.reason
    assert guarded == NodeLabels((), (), {})


def test_node_body_change_keeps_root_when_schema_unchanged() -> None:
    """Matrix: real fingerprint root with unchanged channels — keep the label."""
    labels = NodeLabels(root=("a",), propagated=("c",), inconclusive={})
    base_g = _reducer_graph("t133_keep_a", "operator.add")
    guarded, bad = guard_labels(
        labels,
        graphs=({"a", "b", "c"}, {"a", "b", "c"}),
        counts=({"a": 1, "b": 1, "c": 1}, {"a": 1, "b": 1, "c": 1}),
        channels=(channel_fingerprints(base_g), channel_fingerprints(base_g)),
    )
    assert bad is None
    assert guarded.root == ("a",)


def test_reducer_guard_needs_known_equal_graph_shapes() -> None:
    """Case 3 only when both graphs are known and the same nodes. Unknown base shape (no
    cassette `graph`) means base's live channels may be a different agent than the recording —
    do not call that a schema change and wipe an unexplained label. Differing shapes with no
    sided add/delete label are case 2's concern, not reducer's."""
    labels = NodeLabels(root=(), propagated=(), inconclusive={"worker": UNEXPLAINED})
    base_g = _reducer_graph("t133_skip_base", "operator.add")
    head_g = _reducer_graph("t133_skip_head", "replace")
    channels = (channel_fingerprints(base_g), channel_fingerprints(head_g))
    assert channels[0] != channels[1]
    head_nodes = {"plan", "worker", "collect"}
    kept, bad = guard_labels(labels, graphs=(None, head_nodes), channels=channels)
    assert bad is None
    assert kept == labels
    # Shapes differ but the labelled node is on both sides — not rename-like; still not case 3.
    shared = NodeLabels(root=(), propagated=(), inconclusive={"mid": UNEXPLAINED})
    differing, also = guard_labels(
        shared, graphs=({"old", "mid"}, {"mid", "new"}), channels=channels
    )
    assert also is None
    assert differing == shared
