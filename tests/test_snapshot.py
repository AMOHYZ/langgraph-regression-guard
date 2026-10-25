"""Tests for T050 (snapshot adapter) and T051 (JSON round-trip)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fixtures.build_examples import build_conditional, build_linear

from lgcov.snapshot import Edge, GraphSnapshot, snapshot_from_compiled

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---- T050: snapshot adapter -------------------------------------------------


def test_linear_fixture_shape() -> None:
    snap = snapshot_from_compiled(build_linear())
    assert snap.nodes == frozenset({"__start__", "a", "b", "__end__"})
    assert len(snap.edges) == 3
    assert not any(e.conditional for e in snap.edges)


def test_conditional_edges_are_flagged() -> None:
    snap = snapshot_from_compiled(build_conditional())
    conditional = {(e.source, e.target) for e in snap.edges if e.conditional}
    assert conditional == {("router", "tool"), ("router", "answer")}
    assert Edge("__start__", "router", conditional=False) in snap.edges


def test_snapshot_matches_raw_t012_dump() -> None:
    for name, builder in (("linear", build_linear), ("conditional", build_conditional)):
        raw = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        snap = snapshot_from_compiled(builder())
        assert sorted(snap.nodes) == raw["nodes"]
        assert [e.to_dict() for e in snap.sorted_edges()] == raw["edges"]


# ---- T051: JSON round-trip --------------------------------------------------


# Builders return Any-typed compiled graphs (see fixtures.build_examples).
@pytest.mark.parametrize("builder", [build_linear, build_conditional])
def test_round_trip(builder: Callable[[], Any]) -> None:
    snap = snapshot_from_compiled(builder())
    assert GraphSnapshot.from_json(snap.to_json()) == snap


def test_to_json_is_stable() -> None:
    a = snapshot_from_compiled(build_conditional()).to_json()
    b = snapshot_from_compiled(build_conditional()).to_json()
    assert a == b


def test_linear_golden_file() -> None:
    golden = (FIXTURES / "linear.golden.json").read_text(encoding="utf-8")
    assert snapshot_from_compiled(build_linear()).to_json() == golden


def test_from_json_rejects_unknown_version() -> None:
    bad = json.dumps({"version": 99, "nodes": [], "edges": []})
    with pytest.raises(ValueError, match="version"):
        GraphSnapshot.from_json(bad)


def test_from_json_rejects_edge_to_unknown_node() -> None:
    bad = json.dumps(
        {
            "version": 1,
            "nodes": ["a"],
            "edges": [{"source": "a", "target": "zz", "conditional": False}],
        }
    )
    with pytest.raises(ValueError, match="unknown node"):
        GraphSnapshot.from_json(bad)
