"""graph-diff CLI: print node/edge changes between two graph snapshots."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lgcov.graph_diff import diff_graphs
from lgcov.snapshot import Edge, GraphSnapshot

# 0 = no change, 1 = graph changed, 2 = bad input (missing/unreadable/invalid).
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


def main(argv: list[str] | None = None) -> int:
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
