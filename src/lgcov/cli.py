"""graph-diff CLI: print node/edge changes between two graph snapshots."""

import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import sys
from dataclasses import asdict, replace
from importlib.metadata import version
from pathlib import Path
from types import CodeType
from typing import Any

from lgcov.cassette import Cassette, Drift, Step, accept_nodes, record, replay, replay_steps
from lgcov.ci_report import CiResult, render_ci_report
from lgcov.config import DEFAULT_PATH as DEFAULT_CONFIG
from lgcov.config import load_config
from lgcov.coverage import (
    CoverageSnapshot,
    coverage_delta,
    format_coverage_line,
    snapshot_coverage,
)
from lgcov.drift_gate import FailOn, drift_exit_code, parse_fail_on, verdict
from lgcov.graph_diff import GraphDiff, diff_graphs
from lgcov.snapshot import Edge, GraphSnapshot, snapshot_from_compiled
from lgcov.taxonomy import load_taxonomy

# 0 = no change / accepted, 1 = graph/replay changed or named node not drifted,
# 2 = bad input (missing/unreadable/invalid).
EXIT_ERROR = 2
EXIT_NOT_DRIFTED = 1


def _output_hash(output: object) -> str:
    body = json.dumps(output, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:12]


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


class _SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    """Always compile from source, never from __pycache__: a .pyc is reused when the
    source keeps its size and second-resolution mtime, which a quick same-length edit
    (A1 -> A2, a prompt tweak) does, and replay would then run the old graph.
    """

    def get_code(self, fullname: str) -> CodeType:
        return self.source_to_code(self.get_data(self.path), self.path)


# Any: a compiled graph from user code; see cassette.record for why it stays untyped.
def _load_graph(target: str) -> Any:
    path, sep, attr = target.rpartition(":")
    if not sep or not path or not attr:
        raise ValueError(f"target must be <file.py>:<attr>, got {target!r}")
    name = Path(path).stem
    spec = importlib.util.spec_from_file_location(name, path, loader=_SourceOnlyLoader(name, path))
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


def _drift_kinds(
    runs: list[Cassette], drifts: list[list[Drift]]
) -> tuple[dict[str, int], set[str]]:
    """Runs each node drifted in, and the root nodes. "root" = the first node to drift
    in some run, in execution order; "downstream" nodes drifted only after an earlier one.
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
    return counts, roots


def _replay_markdown(runs: list[Cassette], drifts: list[list[Drift]]) -> list[str]:
    """Per-node drift table: root or downstream, and in how many runs."""
    counts, roots = _drift_kinds(runs, drifts)
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


def _harness_message(what: str, exc: BaseException) -> str:
    return f"{what}: {type(exc).__name__}: {exc}"


def _harness_error(prog: str, what: str, exc: BaseException) -> int:
    """Infra failures exit 2 and say so, so CI never reports them as a regression."""
    msg = f"{prog}: harness error, not a regression: {_harness_message(what, exc)}"
    print(msg, file=sys.stderr)
    return EXIT_ERROR


_LIVE_HELP = "re-run every node; by default a node whose code and input match is served"


def _fail_on_arg(value: str) -> FailOn:
    try:
        return parse_fail_on(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _replay(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="lgcov replay")
    ap.add_argument("target", nargs="?", help="<file.py>:<attr>; overrides graph_entrypoint")
    ap.add_argument(
        "baseline", nargs="?", help="cassette JSON; default <cassette_dir>/baseline.json"
    )
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="lgcov.yaml (optional)")
    ap.add_argument("--fail-on", type=_fail_on_arg, default="any-drift", help="exit 1 on")
    ap.add_argument("--summary-json", help="also write drift counts and verdict here")
    ap.add_argument("--live", action="store_true", help=_LIVE_HELP)
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config, {"graph_entrypoint": args.target})
    except (OSError, ValueError) as exc:
        print(f"lgcov replay: invalid config: {exc}", file=sys.stderr)
        return EXIT_ERROR
    baseline = args.baseline or str(Path(cfg.cassette_dir) / "baseline.json")
    try:
        # Exception: importing user graph code can raise anything.
        graph = _load_graph(cfg.graph_entrypoint)
    except Exception as exc:
        return _harness_error("lgcov replay", "cannot load graph", exc)
    try:
        runs = _load_runs(baseline)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _harness_error("lgcov replay", "cannot read baseline", exc)
    try:
        drifts = [replay(graph, run, cfg.ignore_paths, serve=not args.live) for run in runs]
    except Exception as exc:  # a node raised; we can't tell a PR bug from a CI problem
        return _harness_error("lgcov replay", "the graph crashed during replay", exc)
    print("\n".join(_replay_markdown(runs, drifts)))
    counts, roots = _drift_kinds(runs, drifts)
    root, downstream = sorted(roots), sorted(counts.keys() - roots)
    code = drift_exit_code(args.fail_on, len(root), len(downstream))
    if args.summary_json:
        summary = {
            "exit_code": code,
            "fail_on": args.fail_on,
            "propagated_drift": downstream,
            "propagated_drift_count": len(downstream),
            "root_drift": root,
            "root_drift_count": len(root),
            "runs": len(runs),
            "verdict": verdict(code),
        }
        Path(args.summary_json).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return code


def _snapshot(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="lgcov snapshot")
    ap.add_argument("--graph", help="<file.py>:<attr>; overrides graph_entrypoint")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="lgcov.yaml (optional)")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config, {"graph_entrypoint": args.graph})
    except (OSError, ValueError) as exc:
        print(f"lgcov snapshot: invalid config: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        # Exception: importing user graph code can raise anything.
        snap = snapshot_from_compiled(_load_graph(cfg.graph_entrypoint))
    except Exception as exc:
        return _harness_error("lgcov snapshot", "cannot load graph", exc)
    print(snap.to_json(), end="")
    return 0


def _coverage_cmd(argv: list[str]) -> int:
    """T083: coverage of the recorded runs (nothing re-runs), and its trend vs main."""
    ap = argparse.ArgumentParser(prog="lgcov coverage")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="lgcov.yaml (optional)")
    ap.add_argument("--graph", help="<file.py>:<attr>; overrides graph_entrypoint")
    ap.add_argument("--cassette-dir", help="overrides cassette_dir")
    ap.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""), help="commit SHA")
    ap.add_argument("--out", help="write coverage.json here")
    ap.add_argument("--baseline", help="main's coverage.json; a missing file = no baseline yet")
    args = ap.parse_args(argv)
    overrides = {"graph_entrypoint": args.graph, "cassette_dir": args.cassette_dir}
    try:
        cfg = load_config(args.config, overrides)
    except (OSError, ValueError) as exc:
        print(f"lgcov coverage: invalid config: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        # Exception: importing user graph code can raise anything.
        snap = snapshot_from_compiled(_load_graph(cfg.graph_entrypoint))
    except Exception as exc:
        return _harness_error("lgcov coverage", "cannot load graph", exc)
    try:
        runs = _load_runs(str(Path(cfg.cassette_dir) / "baseline.json"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _harness_error("lgcov coverage", "cannot read baseline", exc)
    cov = snapshot_coverage(snap, [[s.node for s in r.steps] for r in runs], args.commit)
    base = None
    if args.baseline and Path(args.baseline).is_file():
        try:
            base = CoverageSnapshot.from_json(Path(args.baseline).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"lgcov coverage: cannot read baseline coverage: {exc}", file=sys.stderr)
            return EXIT_ERROR
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(cov.to_json(), encoding="utf-8")
    print(format_coverage_line(cov, base))
    newly = coverage_delta(base, cov).newly_uncovered if base else ()
    if newly:
        print("Newly uncovered: " + ", ".join(f"`{n}`" for n in newly))
    return 0


def _write_runs(path: Path, runs: list[Cassette]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same shape and formatting as examples/demo_agent/record.py, so baselines diff cleanly.
    body = json.dumps({"runs": [asdict(r) for r in runs]}, indent=2, sort_keys=True) + "\n"
    path.write_text(body, encoding="utf-8")


def _record(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="lgcov record")
    ap.add_argument("--inputs", required=True, help="JSON list of graph input objects")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="lgcov.yaml (optional)")
    ap.add_argument("--graph", help="<file.py>:<attr>; overrides graph_entrypoint")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config, {"graph_entrypoint": args.graph})
        graph = _load_graph(cfg.graph_entrypoint)
        # Any: raw JSON before validation
        inputs: Any = json.loads(Path(args.inputs).read_text(encoding="utf-8"))
        if not isinstance(inputs, list) or not all(isinstance(i, dict) for i in inputs):
            raise ValueError(f"{args.inputs}: must be a JSON list of input objects")
    except (OSError, ImportError, SyntaxError, ValueError) as exc:
        print(f"lgcov record: {exc}", file=sys.stderr)
        return EXIT_ERROR
    runs = [record(graph, i) for i in inputs]
    out = Path(cfg.cassette_dir) / "baseline.json"
    _write_runs(out, runs)
    print(f"recorded {len(runs)} runs to {out}")
    return 0


def _accept_summary(
    path: Path,
    runs: list[Cassette],
    fresh: list[Cassette],
    results: list[tuple[Cassette, set[str]]],
    nodes: set[str],
) -> str:
    """Markdown table: node, old/new output hash, file touched."""
    lines = [
        "### Cassette accept",
        "",
        "| node | old | new | file |",
        "|---|---|---|---|",
    ]
    for node in sorted(nodes):
        if not any(node in changed for _, changed in results):
            continue
        old_hashes: list[str] = []
        new_hashes: list[str] = []
        for base, neu in zip(runs, fresh, strict=True):
            for step in base.steps:
                if step.node == node:
                    old_hashes.append(_output_hash(step.output))
            for step in neu.steps:
                if step.node == node:
                    new_hashes.append(_output_hash(step.output))
        old = ",".join(dict.fromkeys(old_hashes)) or "—"
        new = ",".join(dict.fromkeys(new_hashes)) or "—"
        lines.append(f"| `{node}` | `{old}` | `{new}` | `{path}` |")
    return "\n".join(lines) + "\n"


def _cassette_accept(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="lgcov cassette")
    ap.add_argument("action", choices=["accept"])
    ap.add_argument(
        "--nodes",
        help="comma-separated drifted nodes to re-baseline, e.g. respond,tool",
    )
    ap.add_argument(
        "--all-drifted",
        action="store_true",
        help="re-baseline every node that currently drifts",
    )
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="lgcov.yaml (optional)")
    ap.add_argument("--cassette-dir", help="overrides cassette_dir from config")
    args = ap.parse_args(argv)
    if bool(args.nodes) == args.all_drifted:
        print(
            "lgcov cassette accept: pass exactly one of --nodes or --all-drifted",
            file=sys.stderr,
        )
        return EXIT_ERROR
    named = {n for n in (args.nodes or "").split(",") if n}
    try:
        cfg = load_config(args.config, {"cassette_dir": args.cassette_dir})
        path = Path(cfg.cassette_dir) / "baseline.json"
        runs = _load_runs(str(path))
        graph = _load_graph(cfg.graph_entrypoint)
        fresh = [record(graph, run.inputs) for run in runs]
        if args.all_drifted:
            nodes = {
                node
                for base, neu in zip(runs, fresh, strict=True)
                for node in accept_nodes(base, neu, {s.node for s in base.steps})[1]
            }
            if not nodes:
                print("lgcov cassette accept: no drifted nodes", file=sys.stderr)
                return EXIT_NOT_DRIFTED
        else:
            nodes = named
            known = {s.node for r in (*runs, *fresh) for s in r.steps}
            unknown = sorted(nodes - known)
            if unknown:
                raise ValueError(f"unknown node {unknown[0]!r}; known: {sorted(known)}")
        results = [accept_nodes(b, f, nodes) for b, f in zip(runs, fresh, strict=True)]
    except (OSError, ImportError, SyntaxError, ValueError, KeyError, TypeError) as exc:
        print(f"lgcov cassette accept: {exc}", file=sys.stderr)
        return EXIT_ERROR
    not_drifted = sorted(n for n in nodes if not any(n in c for _, c in results))
    if not_drifted and not args.all_drifted:
        print(
            f"lgcov cassette accept: node {not_drifted[0]!r} is not drifted",
            file=sys.stderr,
        )
        return EXIT_NOT_DRIFTED
    changed_nodes = {n for _, c in results for n in c}
    if not changed_nodes:
        print("lgcov cassette accept: no drifted nodes", file=sys.stderr)
        return EXIT_NOT_DRIFTED
    _write_runs(path, [accepted for accepted, _ in results])
    summary = _accept_summary(path, runs, fresh, results, changed_nodes)
    summary_path = path.parent / "accept.md"
    summary_path.write_text(summary, encoding="utf-8")
    print(summary, end="")
    print(f"wrote {path}")
    print(f"wrote {summary_path}")
    return 0


def _graph_diff_lines(diff: GraphDiff) -> list[str]:
    if diff.is_empty:
        return ["No graph changes."]
    rows = [("+", "node", n) for n in sorted(diff.added_nodes)]
    rows += [("-", "node", n) for n in sorted(diff.removed_nodes)]
    rows += [("+", "edge", f"{e.source} → {e.target}") for e in sorted(diff.added_edges)]
    rows += [("-", "edge", f"{e.source} → {e.target}") for e in sorted(diff.removed_edges)]
    return ["| | kind | item |", "|---|---|---|", *(f"| {s} | {k} | {i} |" for s, k, i in rows)]


def _in_dir(base_dir: str, target: str) -> str | None:
    """The same graph file inside the base checkout; None if it lies outside the repo."""
    path, _, attr = target.rpartition(":")
    rel = Path(path)
    if rel.is_absolute():
        try:
            rel = rel.relative_to(Path.cwd())
        except ValueError:
            return None
    return f"{Path(base_dir) / rel}:{attr}"


def _base_diff(base_dir: str | None, target: str, head: GraphSnapshot) -> tuple[list[str], str]:
    """Graph diff of base vs head. A base that can't be loaded is noted, not an error."""
    if not base_dir:
        return [], "No base checkout given; graph diff skipped."
    base_target = _in_dir(base_dir, target)
    if base_target is None:
        return [], "The graph file is outside the repo; graph diff skipped."
    if not Path(base_target.rpartition(":")[0]).is_file():
        empty = GraphSnapshot(nodes=frozenset())
        note = "No graph at this path on the base branch, so every node is new."
        return _graph_diff_lines(diff_graphs(empty, head)), note
    try:
        # Exception: the base graph's own code or deps; not this PR's problem.
        base = snapshot_from_compiled(_load_graph(base_target))
    except Exception as exc:
        return [], f"Could not load the base graph ({_harness_message('load', exc)}); skipped."
    return _graph_diff_lines(diff_graphs(base, head)), ""


def _ci_result(args: argparse.Namespace) -> CiResult:
    fail_on: FailOn = args.fail_on
    if args.harness_error:
        return CiResult(fail_on, EXIT_ERROR, harness_error=args.harness_error)
    overrides = {"graph_entrypoint": args.graph, "cassette_dir": args.cassette_dir}
    try:
        cfg = load_config(args.config, overrides)
    except (OSError, ValueError) as exc:
        return CiResult(fail_on, EXIT_ERROR, harness_error=f"invalid lgcov.yaml: {exc}")
    notes: list[str] = []
    if args.taxonomy:
        try:
            tax = load_taxonomy(args.taxonomy)
        except (OSError, ValueError) as exc:
            return CiResult(fail_on, EXIT_ERROR, harness_error=f"invalid taxonomy: {exc}")
        notes.append(
            f"Taxonomy `{args.taxonomy}`: {len(tax.kpi_specs)} KPIs, validated. "
            "L2 judges don't run in the Action yet."
        )
    try:
        # Exception: importing user graph code can raise anything.
        graph = _load_graph(cfg.graph_entrypoint)
        head = snapshot_from_compiled(graph)
    except Exception as exc:
        return CiResult(
            fail_on, EXIT_ERROR, harness_error=_harness_message("cannot load graph", exc)
        )
    graph_lines, graph_note = _base_diff(args.base_dir, cfg.graph_entrypoint, head)
    partial = CiResult(fail_on, EXIT_ERROR, tuple(graph_lines), graph_note, notes=tuple(notes))
    try:
        runs = _load_runs(str(Path(cfg.cassette_dir) / "baseline.json"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return replace(partial, harness_error=_harness_message("cannot read baseline", exc))
    try:
        replays = [replay_steps(graph, run, cfg.ignore_paths, serve=not args.live) for run in runs]
    except Exception as exc:  # a node raised; we can't tell a PR bug from a CI problem
        msg = _harness_message("the graph crashed during replay", exc)
        return replace(partial, harness_error=msg)
    drifts = [d for d, _ in replays]
    counts, roots = _drift_kinds(runs, drifts)
    root, downstream = sorted(roots), sorted(counts.keys() - roots)
    # T083: coverage of the runs replay just did; nothing executes again for it.
    cov = snapshot_coverage(head, [[s.node for s in steps] for _, steps in replays], args.commit)
    if args.coverage_out:
        Path(args.coverage_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.coverage_out).write_text(cov.to_json(), encoding="utf-8")
    base, note = _coverage_baseline(args.coverage_baseline)
    return replace(
        partial,
        exit_code=drift_exit_code(fail_on, len(root), len(downstream)),
        replay_lines=tuple(_replay_markdown(runs, drifts)),
        root_drift=tuple(root),
        propagated_drift=tuple(downstream),
        coverage_line=format_coverage_line(cov, base),
        newly_uncovered=coverage_delta(base, cov).newly_uncovered if base else (),
        notes=(*partial.notes, note) if note else partial.notes,
    )


def _coverage_baseline(path: str | None) -> tuple[CoverageSnapshot | None, str]:
    """Main's coverage.json if there is a readable one. A bad file is a note, never an error."""
    if not path or not Path(path).is_file():
        return None, ""
    try:
        return CoverageSnapshot.from_json(Path(path).read_text(encoding="utf-8")), ""
    except (OSError, ValueError) as exc:
        return None, f"Could not read main's coverage.json ({exc}); showing no baseline yet."


def _ci(argv: list[str]) -> int:
    """T078: one run for the GitHub Action: report file, step outputs, exit code."""
    ap = argparse.ArgumentParser(prog="lgcov ci")
    ap.add_argument("--report", required=True, help="write the PR comment markdown here")
    ap.add_argument("--github-output", help="append step outputs here ($GITHUB_OUTPUT)")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="lgcov.yaml (optional)")
    ap.add_argument("--graph", help="<file.py>:<attr>; overrides graph_entrypoint")
    ap.add_argument("--cassette-dir", help="overrides cassette_dir")
    ap.add_argument("--taxonomy", help="validate this taxonomy.yaml (L2 doesn't run yet)")
    ap.add_argument("--base-dir", help="checkout of the PR's base commit, for the graph diff")
    ap.add_argument("--fail-on", type=_fail_on_arg, default="root-drift", help="exit 1 on")
    ap.add_argument("--harness-error", help="an earlier step failed; report it and exit 2")
    ap.add_argument("--live", action="store_true", help=_LIVE_HELP)
    ap.add_argument("--coverage-out", help="write this run's coverage.json here")
    ap.add_argument("--coverage-baseline", help="main's coverage.json; missing = no baseline yet")
    ap.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""), help="commit SHA")
    args = ap.parse_args(argv)
    result = _ci_result(args)
    report = Path(args.report).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    body = render_ci_report(result)
    report.write_text(body, encoding="utf-8")
    print(body, end="")
    if args.github_output:
        outputs = {
            "exit-code": result.exit_code,
            "propagated-drift-count": len(result.propagated_drift),
            "report-path": report,
            "root-drift-count": len(result.root_drift),
            "verdict": verdict(result.exit_code),
        }
        with open(args.github_output, "a", encoding="utf-8") as f:
            f.writelines(f"{k}={v}\n" for k, v in outputs.items())
    return result.exit_code


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--version"]:
        print(f"lgcov {version('langgraph-regression-guard')}")
        return 0
    if argv[:1] == ["taxonomy"]:
        return _taxonomy_check(argv[1:])
    if argv[:1] == ["replay"]:
        return _replay(argv[1:])
    if argv[:1] == ["snapshot"]:
        return _snapshot(argv[1:])
    if argv[:1] == ["coverage"]:
        return _coverage_cmd(argv[1:])
    if argv[:1] == ["ci"]:
        return _ci(argv[1:])
    if argv[:1] == ["record"]:
        return _record(argv[1:])
    if argv[:1] == ["cassette"]:
        return _cassette_accept(argv[1:])
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
    print("\n".join(_graph_diff_lines(diff)))
    return 0 if diff.is_empty else 1


if __name__ == "__main__":
    raise SystemExit(main())
