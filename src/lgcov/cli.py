"""graph-diff CLI: print node/edge changes between two graph snapshots."""

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from lgcov.cassette import Cassette, Drift, Step, replay
from lgcov.graph_diff import diff_graphs
from lgcov.snapshot import Edge, GraphSnapshot
from lgcov.taxonomy import load_taxonomy

# 0 = no change, 1 = graph (or replay) changed, 2 = bad input (missing/unreadable/invalid).
EXIT_ERROR = 2


def _load(path: str) -> GraphSnapshot:
    # Any: raw JSON before validation
    d: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(d, dict) and "version" in d:
        return GraphSnapshot.from_dict(d)  # new format (to_json output)
    # legacy format: {"nodes": [...], "edges": [[a, b], ...]}
    return GraphSnapshot(
        nodes=frozenset(d["nodes"]),
        edges=frozenset(Edge(str(a), str(b)) for a, b in d["edges"]),
    )


def _taxonomy_check(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="lgcov taxonomy")
    ap.add_argument("action", choices=["check"])
    ap.add_argument("path")
    args = ap.parse_args(argv)
    try:
        tax = load_taxonomy(args.path)
    except (OSError, ValueError) as exc:
        print(f"lgcov taxonomy: invalid taxonomy: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"{tax.system}: {len(tax.kpi_specs)} KPIs")
    print("| name | node | judge | labels |")
    print("|---|---|---|---|")
    for spec in tax.kpi_specs:
        print(f"| {spec.name} | {spec.node} | {spec.judge} | {', '.join(spec.labels)} |")
    return 0


# Any: a compiled graph from user code; see cassette.record for why it stays untyped.
def _load_graph(target: str) -> Any:
    path, sep, attr = target.rpartition(":")
    if not sep or not path or not attr:
        raise ValueError(f"target must be <file.py>:<attr>, got {target!r}")
    spec = importlib.util.spec_from_file_location(Path(path).stem, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    graph = getattr(module, attr, None)
    if not hasattr(graph, "stream"):
        raise ValueError(f"{target} is not a compiled graph")
    return graph


def _load_runs(path: str) -> list[Cassette]:
    # Any: raw JSON before validation
    d: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Cassette(inputs=r["inputs"], steps=[Step(**s) for s in r["steps"]]) for r in d["runs"]]


def _replay_markdown(runs: list[Cassette], drifts: list[list[Drift]]) -> list[str]:
    """Per-node drift table. "root" = the first node to drift in some run, in execution
    order; "downstream" nodes drifted only after an earlier one did.
    """
    counts: dict[str, int] = {}
    roots: set[str] = set()
    for run, run_drifts in zip(runs, drifts, strict=True):
        drifted = {d.node for d in run_drifts}
        for node in drifted:
            counts[node] = counts.get(node, 0) + 1
        # nodes the change added are not in the baseline steps; they sort last
        ordered = [s.node for s in run.steps if s.node in drifted] + sorted(drifted)
        if ordered:
            roots.add(ordered[0])
    lines = ["### Cassette replay", ""]
    if not counts:
        return [*lines, f"No cassette drift ({len(runs)} runs)."]
    lines += ["| node | drift | runs |", "|---|---|---|"]
    for node in sorted(counts):
        kind = "root" if node in roots else "downstream"
        lines.append(f"| `{node}` | {kind} | {counts[node]}/{len(runs)} |")
    unchanged = sorted({s.node for r in runs for s in r.steps} - counts.keys())
    if unchanged:
        lines += ["", "Unchanged: " + ", ".join(f"`{n}`" for n in unchanged)]
    return lines


def _replay(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="lgcov replay")
    ap.add_argument("target", help="<file.py>:<attr> of the compiled graph")
    ap.add_argument("baseline", help='cassette JSON: {"runs": [...]}')
    args = ap.parse_args(argv)
    try:
        graph = _load_graph(args.target)
    except (OSError, ImportError, SyntaxError, ValueError) as exc:
        print(f"lgcov replay: cannot load graph: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        runs = _load_runs(args.baseline)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"lgcov replay: cannot read baseline: {exc!r}", file=sys.stderr)
        return EXIT_ERROR
    drifts = [replay(graph, run) for run in runs]
    print("\n".join(_replay_markdown(runs, drifts)))
    return 1 if any(drifts) else 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["taxonomy"]:
        return _taxonomy_check(argv[1:])
    if argv[:1] == ["replay"]:
        return _replay(argv[1:])
    ap = argparse.ArgumentParser(prog="graph-diff")
    ap.add_argument("old")
    ap.add_argument("new")
    args = ap.parse_args(argv)
    for p in (args.old, args.new):
        if not Path(p).is_file():
            print(f"graph-diff: file not found: {p}", file=sys.stderr)
            return EXIT_ERROR
    try:
        old, new = _load(args.old), _load(args.new)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"graph-diff: cannot read snapshot: {exc}", file=sys.stderr)
        return EXIT_ERROR
    diff = diff_graphs(old, new)
    if diff.is_empty:
        print("No graph changes.")
        return 0
    rows = [("+", "node", n) for n in sorted(diff.added_nodes)]
    rows += [("-", "node", n) for n in sorted(diff.removed_nodes)]
    rows += [("+", "edge", f"{e.source} → {e.target}") for e in sorted(diff.added_edges)]
    rows += [("-", "edge", f"{e.source} → {e.target}") for e in sorted(diff.removed_edges)]
    print("| | kind | item |")
    print("|---|---|---|")
    for sign, kind, item in rows:
        print(f"| {sign} | {kind} | {item} |")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
