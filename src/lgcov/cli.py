"""graph-diff CLI: print node/edge changes between two graph snapshots."""

import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from types import CodeType
from typing import Any

import yaml

from lgcov.calibration import (
    Band,
    Bands,
    JudgeStats,
    Row,
    band_changes,
    bands_document,
    calibrate,
    judge_bands,
    read_labels,
    recorded_bands,
    render_calibration,
    rows_from_overrides,
    saved_case_lines,
    uncalibrated_line,
    upsert_case,
)
from lgcov.cassette import (
    ON_DIVERGENCE,
    TRANSPORT_ERRORS,
    Budget,
    Cassette,
    Drift,
    NodeError,
    OnDivergence,
    Replay,
    Step,
    accept_nodes,
    record,
    replay,
    replay_run,
    replay_steps,
    unservable,
)
from lgcov.ci_report import CiResult, Timing, render_ci_report
from lgcov.code_judges import code_judge
from lgcov.config import DEFAULT_PATH as DEFAULT_CONFIG
from lgcov.config import TIERS, Config, GateConfig, Tier, load_config, tier_policy
from lgcov.coverage import (
    CoverageSnapshot,
    coverage_delta,
    format_coverage_line,
    node_floor_miss,
    snapshot_coverage,
)
from lgcov.drift_gate import FailOn, Outcome, drift_exit_code, parse_fail_on, verdict
from lgcov.graph_diff import GraphDiff, diff_graphs
from lgcov.jev_judge import JevJudge
from lgcov.judges import KPI, FakeJudge, Judge, JudgeInfraError, NodeState
from lgcov.review import ReviewDecision, apply_decision
from lgcov.sampling import (
    KpiStats,
    gate_exit_code,
    gate_kpis,
    judge_samples,
    kpi_stats,
    render_gate_table,
    render_kpi_table,
)
from lgcov.snapshot import Edge, GraphSnapshot, snapshot_from_compiled
from lgcov.taxonomy import KPISpec, load_taxonomy

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
        for node in {d.node for d in run_drifts}:
            counts[node] = counts.get(node, 0) + 1
        root = _run_root(run, run_drifts)
        if root:
            roots.add(root)
    return counts, roots


def _run_root(run: Cassette, run_drifts: list[Drift]) -> str | None:
    drifted = {d.node for d in run_drifts}
    # nodes the change added are not in the baseline steps; they sort last
    ordered = [s.node for s in run.steps if s.node in drifted] + sorted(drifted)
    return ordered[0] if ordered else None


def _replay_markdown(
    runs: list[Cassette], drifts: list[list[Drift]], not_checked: tuple[str, ...] = ()
) -> list[str]:
    """Per-node drift table: root or downstream, and in how many runs."""
    counts, roots = _drift_kinds(runs, drifts)
    lines = ["### Cassette replay", ""]
    if not counts:
        return [*lines, f"No cassette drift ({len(runs)} runs)."]
    lines += ["| node | drift | runs |", "|---|---|---|"]
    for node in sorted(counts):
        kind = "root" if node in roots else "downstream"
        lines.append(f"| `{node}` | {kind} | {counts[node]}/{len(runs)} |")
    unchanged = sorted({s.node for r in runs for s in r.steps} - counts.keys() - set(not_checked))
    if unchanged:
        lines += ["", "Unchanged: " + ", ".join(f"`{n}`" for n in unchanged)]
    return lines


def _divergence_lines(replays: list[Replay], on_divergence: OnDivergence) -> list[str]:
    """T089: the runs that diverged from the recording, and what that did to them."""
    runs = list(enumerate(replays, 1))
    calls = [(f"`{c.node}` in run {i}", c.how) for i, r in runs for c in r.calls]
    first = [where for where, how in calls if how == "diverged"]
    after = [where for where, how in calls if how == "after_divergence"]
    lines = []
    if first and on_divergence == "flag":
        lines.append(f"Diverged, so run live (on-divergence: flag): {', '.join(first)}.")
        lines += [f"Re-executed after divergence: {', '.join(after)}."] if after else []
    if first and on_divergence == "live":
        what = "Diverged, so the rest of the run ran live (on-divergence: live)"
        lines.append(f"{what}: {', '.join(first)}.")
    for why, label in (("diverged", "on-divergence: fail"), ("budget", "budget hit")):
        stopped = [
            f"run {i} from `{r.halt.node}` ({', '.join(f'`{n}`' for n in r.not_checked)})"
            for i, r in runs
            if r.halt is not None and r.halt.why == why
        ]
        lines += [f"Not checked ({label}): {', '.join(stopped)}."] if stopped else []
    return lines


def _harness_message(what: str, exc: BaseException) -> str:
    return f"{what}: {type(exc).__name__}: {exc}"


def _harness_error(prog: str, what: str, exc: BaseException) -> int:
    """Infra failures exit 2 and say so, so CI never reports them as a regression."""
    msg = f"{prog}: harness error, not a regression: {_harness_message(what, exc)}"
    print(msg, file=sys.stderr)
    return EXIT_ERROR


def _node_failure(prog: str, exc: NodeError) -> int:
    """T074: a node's own code raised. AGENT_FAIL whatever fail-on says (it governs drift
    only); network trouble inside the node is INFRA_ERROR instead, and still names the node.
    """
    if exc.transport:
        original = f"{type(exc.original).__name__}: {exc.original}"
        where = f"harness error (transport) in node `{exc.node}`, not a regression"
        print(f"{prog}: {where}: {original}", file=sys.stderr)
        return Outcome.INFRA_ERROR
    print(f"{prog}: agent failure: {exc}")
    return Outcome.AGENT_FAIL


_LIVE_HELP = "re-run every node; by default a node whose code and input match is served"
_INCONCLUSIVE_HELP = "exit 1 on an INCONCLUSIVE blocking KPI; overrides gate.inconclusive_blocks"


def _budget_arg(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        seconds = -1.0
    if not seconds >= 0:  # also catches nan
        raise argparse.ArgumentTypeError(f"must be a number of seconds >= 0, got {value!r}")
    return seconds


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
    except NodeError as exc:
        return _node_failure("lgcov replay", exc)
    except Exception as exc:  # replay itself, or a router / subgraph (not attributed yet, T109)
        return _harness_error("lgcov replay", "replay failed outside node code", exc)
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
    start = perf_counter()
    budget = Budget(None, perf_counter)  # T089: from the start of the run; seconds below
    fail_on: FailOn = args.fail_on
    if args.harness_error:
        return CiResult(fail_on, EXIT_ERROR, harness_error=args.harness_error)
    overrides = {"graph_entrypoint": args.graph, "cassette_dir": args.cassette_dir}
    try:
        cfg = load_config(args.config, overrides)
    except (OSError, ValueError) as exc:
        return CiResult(fail_on, EXIT_ERROR, harness_error=f"invalid lgcov.yaml: {exc}")
    tier: Tier = args.tier or cfg.tier
    policy = tier_policy(tier, cfg.samples_per_case)
    budget.seconds = policy.budget if args.budget_seconds is None else args.budget_seconds
    l2, advisory, flagged, llm_code = None, frozenset[str](), frozenset[str](), 0
    llm_notes: list[str] = []
    calibration: list[str] = []
    if args.taxonomy:
        try:
            every = list(load_taxonomy(args.taxonomy).kpi_specs)
            llm = [s.name for s in every if s.judge == "llm"]
            runs_llm = policy.llm_judges and _llm_judge is not None
            specs = every if runs_llm else [s for s in every if s.judge != "llm"]
            l2 = (specs, [_l2_judge(spec, cfg.jev_model) for spec in specs])
        except (OSError, ValueError) as exc:
            return CiResult(fail_on, EXIT_ERROR, harness_error=f"invalid taxonomy: {exc}")
        try:
            advisory, flagged, calibration = _gate_bands(args.bands, every, cfg)
        except (OSError, ValueError) as exc:
            msg = f"invalid lgcov.yaml or bands.json: {exc}"
            return CiResult(fail_on, EXIT_ERROR, harness_error=msg)
        llm_notes, llm_code = _llm_notes(llm, advisory, policy.llm_judges)
    try:
        # Exception: importing user graph code can raise anything.
        graph = _load_graph(cfg.graph_entrypoint)
        head = snapshot_from_compiled(graph)
    except Exception as exc:
        return CiResult(
            fail_on, EXIT_ERROR, harness_error=_harness_message("cannot load graph", exc)
        )
    graph_lines, graph_note = _base_diff(args.base_dir, cfg.graph_entrypoint, head)
    partial = CiResult(fail_on, EXIT_ERROR, tuple(graph_lines), graph_note)
    try:
        runs = _load_runs(str(Path(cfg.cassette_dir) / "baseline.json"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return replace(partial, harness_error=_harness_message("cannot read baseline", exc))
    serve = policy.serve and not args.live
    budget.expected_live = unservable(graph, runs) if serve else sum(len(r.steps) for r in runs)
    agent_start = perf_counter()
    try:
        replays = [
            replay_run(graph, run, cfg.ignore_paths, serve, args.on_divergence, budget)
            for run in runs
        ]
    except NodeError as exc:
        original = f"{type(exc.original).__name__}: {exc.original}"
        if exc.transport:
            return replace(partial, harness_error=original, harness_node=exc.node)
        return replace(
            partial,
            exit_code=Outcome.AGENT_FAIL,  # whatever fail-on says: fail-on governs drift only
            crash=f"Node `{exc.node}` raised {original}",
            root_drift=(exc.node,),
        )
    except Exception as exc:  # replay itself, or a router / subgraph (not attributed yet, T109)
        msg = _harness_message("replay failed outside node code", exc)
        return replace(partial, harness_error=msg)
    agent = perf_counter() - agent_start
    pairs = [(list(r.drifts), list(r.steps)) for r in replays]
    drifts = [d for d, _ in pairs]
    counts, roots = _drift_kinds(runs, drifts)
    root, downstream = sorted(roots), sorted(counts.keys() - roots)
    # T083: coverage of the runs replay just did; nothing executes again for it.
    cov = snapshot_coverage(head, [[s.node for s in steps] for _, steps in pairs], args.commit)
    if args.coverage_out:
        Path(args.coverage_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.coverage_out).write_text(cov.to_json(), encoding="utf-8")
    base, note = _coverage_baseline(args.coverage_baseline)
    # T089: nodes a run stopped before (divergence or budget) are INCONCLUSIVE, never a fail.
    not_checked = tuple(dict.fromkeys(n for r in replays for n in r.not_checked))
    blocks = args.fail_on_inconclusive or cfg.gate.inconclusive_blocks
    drift_code = drift_exit_code(fail_on, len(root), len(downstream))
    under_floor = node_floor_miss(cov, cfg.thresholds.min_node_coverage)
    floor_code = Outcome.AGENT_FAIL if under_floor else 0  # a floor miss is 1, never 2
    l1_code = max(drift_code, int(blocks and bool(not_checked)), floor_code)
    diverged = _divergence_lines(replays, args.on_divergence)
    calls = [c for r in replays for c in r.calls]
    timing = Timing(
        0.0,
        agent,
        live=sum(1 for c in calls if c.how != "served"),
        served=sum(1 for c in calls if c.how == "served"),
        budget=budget.seconds,
        budget_hit=budget.hit,
    )
    result = replace(
        partial,
        exit_code=l1_code,
        replay_lines=(
            *_replay_markdown(runs, drifts, not_checked),
            *([""] if diverged else []),
            *diverged,
        ),
        root_drift=tuple(root),
        propagated_drift=tuple(downstream),
        coverage_line=format_coverage_line(cov, base),
        newly_uncovered=coverage_delta(base, cov).newly_uncovered if base else (),
        under_floor=under_floor,
        notes=(
            *llm_notes,
            *([note] if note else []),
            *([f"Latest nightly run on main: {args.nightly_url}"] if args.nightly_url else []),
        ),
        tier=tier,
        not_checked=not_checked,
        timing=replace(timing, total=perf_counter() - start),
    )
    try:
        changes = _calibration_changes(args, l2[0] if l2 else [], cfg)
    except (OSError, ValueError) as exc:  # a broken label file in the PR: say so, don't fail
        changes = [f"⚠️ could not recompute the bands from {Path(args.bands).parent}/: {exc}"]
    result = replace(result, calibration_lines=tuple(changes))
    if l2 is None:
        return result
    judge_start = perf_counter()
    try:
        saved = _saved_cases(Path(args.bands).parent / "cases", l2[0])
        extra = sum(len(saved.get(s.name, [])) for s in l2[0] if s.judge != "code")
        stats, gate = _ci_gate(args, cfg, policy.k, advisory, flagged, *l2, runs, pairs, extra)
    except Exception as exc:  # a judge's own bug or the call limit: harness, not the PR's graph
        msg = _harness_message("L2 gate failed", exc)
        return replace(result, exit_code=l1_code or EXIT_ERROR, harness_error=msg)
    tokens = [s.tokens_total for s in stats if s.tokens_total]  # code judges report 0
    judged = replace(
        timing,
        judges=perf_counter() - judge_start,
        judge_calls=sum(s.samples for s in stats),
        tokens=sum(tokens) if tokens else None,
    )
    gate_code = gate_exit_code(stats, gate.inconclusive_blocks)
    return replace(
        result,
        exit_code=1 if 1 in (l1_code, gate_code) else max(l1_code, gate_code, llm_code),
        l2_lines=(
            *calibration,
            *([""] if calibration and stats else []),
            *(
                (*render_gate_table(stats, gate.alpha, gate.power), "", *render_kpi_table(stats))
                if stats
                else ()
            ),
        ),
        blocked=tuple(s.kpi for s in stats if s.blocking and s.outcome == "fail"),
        inconclusive=tuple(s.kpi for s in stats if s.blocking and s.outcome == "needs_human"),
        timing=replace(judged, total=perf_counter() - start),
        calibration_lines=(*changes, *_rejudge(saved, l2[0], l2[1])),
    )


def _llm_notes(
    llm: list[str], advisory: frozenset[str], tier_runs_them: bool
) -> tuple[list[str], int]:
    """T089: the `judge: llm` KPIs this run won't judge, and the exit code that leaves: on
    nightly, INFRA_ERROR when one with no client blocks; an advisory one is only shown."""
    if not llm or (tier_runs_them and _llm_judge is not None):
        return [], 0
    if not tier_runs_them:
        return [f"Nightly only (`judge: llm`): {_quoted(llm)}."], 0
    blocking = [n for n in llm if n not in advisory]
    notes = [f"⚠️ No llm client for {_quoted(blocking)}: INFRA_ERROR."] if blocking else []
    quiet = [n for n in llm if n in advisory]
    notes += [f"⚠️ Skipped, no llm client: {_quoted(quiet)} (advisory)."] if quiet else []
    return notes, EXIT_ERROR if blocking else 0


def _quoted(names: list[str]) -> str:
    return ", ".join(f"`{n}`" for n in names)


def _ci_gate(
    args: argparse.Namespace,
    cfg: Config,
    k: int,
    advisory: frozenset[str],
    flagged: frozenset[str],
    specs: list[KPISpec],
    judges: list[Judge],
    runs: list[Cassette],
    replays: list[tuple[list[Drift], list[Step]]],
    extra: int = 0,
) -> tuple[list[KpiStats], GateConfig]:
    """T091: the L2 gate on recorded vs replayed outputs, as `lgcov judge` runs it. `extra`:
    model judge calls on top of the plan (T102: saved cases)."""
    plan = _l2_plan(specs, runs, replays)
    _, model = _l2_calls(plan, k)
    model += extra
    if model > args.max_calls:
        raise ValueError(
            f"refusing to make {model} model judge calls (more than --max-calls "
            f"{args.max_calls}); lower samples_per_case or raise --max-calls"
        )
    gate = replace(cfg.gate, inconclusive_blocks=True) if args.fail_on_inconclusive else cfg.gate
    return _l2_gate(plan, judges, k, gate, runs, replays, advisory, flagged), gate


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
    ap.add_argument("--taxonomy", help="taxonomy.yaml: gate its KPIs (L2) on the replay")
    ap.add_argument("--base-dir", help="checkout of the PR's base commit, for the graph diff")
    ap.add_argument("--fail-on", type=_fail_on_arg, default="root-drift", help="exit 1 on")
    ap.add_argument("--harness-error", help="an earlier step failed; report it and exit 2")
    ap.add_argument("--live", action="store_true", help=_LIVE_HELP)
    ap.add_argument("--coverage-out", help="write this run's coverage.json here")
    ap.add_argument("--coverage-baseline", help="main's coverage.json; missing = no baseline yet")
    ap.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""), help="commit SHA")
    ap.add_argument("--max-calls", type=int, default=200, help="refuse more model judge calls")
    ap.add_argument("--fail-on-inconclusive", action="store_true", help=_INCONCLUSIVE_HELP)
    ap.add_argument(
        "--on-divergence",
        choices=ON_DIVERGENCE,
        default="flag",
        help="a served node's input no longer matches: flag (run it live), live (run the "
        "rest of the run live) or fail (leave the rest of the run not checked)",
    )
    ap.add_argument(
        "--budget-seconds",
        type=_budget_arg,
        help="stop starting live node calls once the run is projected to take longer; "
        "default: the tier's (90 on pr, none on nightly)",
    )
    ap.add_argument("--tier", choices=TIERS, help="pr (default) or nightly; overrides tier")
    ap.add_argument("--nightly-url", help="main's latest nightly run, linked in the report")
    ap.add_argument("--timing-out", help="write this run's timing here as JSON (T105 benchmark)")
    ap.add_argument("--bands", default=_BANDS, help="judge bands from `lgcov calibrate`")
    args = ap.parse_args(argv)
    result = _ci_result(args)
    report = Path(args.report).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    body = render_ci_report(result)
    report.write_text(body, encoding="utf-8")
    print(body, end="")
    if args.timing_out and result.timing:
        timing = Path(args.timing_out)
        timing.parent.mkdir(parents=True, exist_ok=True)
        timing.write_text(json.dumps(asdict(result.timing)) + "\n", encoding="utf-8")
    if args.github_output:
        outputs = {
            "exit-code": result.exit_code,
            "propagated-drift-count": len(result.propagated_drift),
            "report-path": report,
            "root-drift-count": len(result.root_drift),
            "tier": result.tier,
            "verdict": verdict(result.exit_code),
        }
        with open(args.github_output, "a", encoding="utf-8") as f:
            f.writelines(f"{k}={v}\n" for k, v in outputs.items())
    return result.exit_code


_FAKE_CHECK_KEYS = ("fail_every", "when_input_contains")
# T089: the general LLM judge client for `judge: llm` KPIs. None is built in yet: the pr tier
# never runs one, and a nightly run reports the KPIs it couldn't judge.
_llm_judge: Callable[[KPI], Judge] | None = None


def _gate_bands(
    path: str, specs: list[KPISpec], cfg: Config
) -> tuple[frozenset[str], frozenset[str], list[str]]:
    """T063: the advisory KPIs (lgcov.yaml's, plus judges whose band is advisory), the
    flagged ones, and the report's lines about calibration."""
    file = Path(path)
    raw: object = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else None
    kinds = {s.name: s.judge for s in specs}
    bands = judge_bands(kinds, raw, cfg.jev_model, cfg.gate.uncalibrated)
    advisory = cfg.gate.advisory(list(kinds)) | _in_band(bands, "advisory")
    lines = [uncalibrated_line(bands, kinds, cfg.gate.uncalibrated), *bands.warnings]
    return advisory, _in_band(bands, "flag") - advisory, [line for line in lines if line]


def _in_band(bands: Bands, band: Band) -> frozenset[str]:
    return frozenset(kpi for kpi, b in bands.of.items() if b == band)


def _l2_judge(spec: KPISpec, jev_model: str | None = None) -> Judge:
    kpi = spec.to_kpi()
    if spec.judge == "code":
        return code_judge(kpi, spec.check or {})
    if spec.judge == "llm":
        if _llm_judge is None:
            raise ValueError(f"KPI '{spec.name}': judge llm needs a general LLM judge client")
        return _llm_judge(kpi)
    if spec.judge == "fake":
        check = spec.check or {}
        unknown = sorted(set(check) - set(_FAKE_CHECK_KEYS))
        if unknown:
            raise ValueError(
                f"KPI '{spec.name}': fake check keys {unknown} not in {_FAKE_CHECK_KEYS}"
            )
        every = check.get("fail_every", "0")
        if not every.isdigit():
            raise ValueError(f"KPI '{spec.name}': fail_every must be a whole number")
        when = check.get("when_input_contains", "")
        return FakeJudge(kpi, "pass", fail_every=int(every), when_input_contains=when)
    return JevJudge(kpi, model=jev_model)


_Cases = dict[str, tuple[NodeState, NodeState]]


def _l2_cases(spec: KPISpec, runs: list[list[Step]]) -> _Cases:
    """One case per run: the trace for code judges, else the node's own input and output."""
    cases: _Cases = {}
    for i, steps in enumerate(runs):
        case_id = f"run-{i}/{spec.node}"
        if spec.judge == "code":
            cases[case_id] = ({}, {"trace": {"nodes": [s.node for s in steps]}})
            continue
        if spec.node == "final":
            step = steps[-1] if steps else None
        else:
            step = next((s for s in steps if s.node == spec.node), None)
        if step is not None:
            node_in = step.input if isinstance(step.input, dict) else {}
            node_out = step.output if isinstance(step.output, dict) else {"output": step.output}
            cases[case_id] = (node_in, node_out)
    return cases


def _l2_plan(
    specs: list[KPISpec], runs: list[Cassette], replays: list[tuple[list[Drift], list[Step]]]
) -> list[tuple[KPISpec, _Cases, _Cases]]:
    base, fresh = [r.steps for r in runs], [steps for _, steps in replays]
    return [(spec, _l2_cases(spec, base), _l2_cases(spec, fresh)) for spec in specs]


def _l2_calls(plan: list[tuple[KPISpec, _Cases, _Cases]], k: int) -> tuple[int, int]:
    """Judge calls in all, and those to model judges (every judge but code ones)."""
    total = sum((len(b) + len(n)) * k for _, b, n in plan)
    return total, sum((len(b) + len(n)) * k for spec, b, n in plan if spec.judge != "code")


def _l2_gate(
    plan: list[tuple[KPISpec, _Cases, _Cases]],
    judges: list[Judge],
    k: int,
    gate: GateConfig,
    runs: list[Cassette],
    replays: list[tuple[list[Drift], list[Step]]],
    advisory: frozenset[str],
    flagged: frozenset[str] = frozenset(),
) -> list[KpiStats]:
    """T091: k samples per case on both sides, each case paired, then gated across KPIs."""
    run_roots = [_run_root(r, d) for r, (d, _) in zip(runs, replays, strict=True)]
    stats = []
    for (spec, base, new), judge in zip(plan, judges, strict=True):
        base_samples = {c: judge_samples(judge, i, o, k) for c, (i, o) in base.items()}
        new_samples = {c: judge_samples(judge, i, o, k) for c, (i, o) in new.items()}
        roots = {f"run-{i}/{spec.node}": r for i, r in enumerate(run_roots) if r}
        stats.append(
            kpi_stats(
                spec.to_kpi(),
                k,
                base_samples,
                new_samples,
                gate.margin if spec.margin is None else spec.margin,
                code=spec.judge == "code",
                alpha=gate.alpha,
                power=gate.power,
                case_roots=roots,
            )
        )
    return gate_kpis(stats, advisory, gate.alpha, flagged=flagged)


def _judge_cmd(argv: list[str]) -> int:
    """T073: the taxonomy's judges, k samples per case, on recorded and fresh outputs."""
    ap = argparse.ArgumentParser(prog="lgcov judge")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="lgcov.yaml (optional)")
    ap.add_argument("--graph", help="<file.py>:<attr>; overrides graph_entrypoint")
    ap.add_argument("--cassette-dir", help="overrides cassette_dir")
    ap.add_argument("--taxonomy", help="taxonomy.yaml; overrides taxonomy_path")
    ap.add_argument("--samples", type=int, help="k samples per case; overrides samples_per_case")
    ap.add_argument("--max-calls", type=int, default=200, help="refuse more model judge calls")
    ap.add_argument("--live", action="store_true", help=_LIVE_HELP)
    ap.add_argument("--fail-on-inconclusive", action="store_true", help=_INCONCLUSIVE_HELP)
    ap.add_argument("--bands", default=_BANDS, help="judge bands from `lgcov calibrate`")
    args = ap.parse_args(argv)
    overrides = {"graph_entrypoint": args.graph, "cassette_dir": args.cassette_dir}
    try:
        cfg = load_config(args.config, overrides)
    except (OSError, ValueError) as exc:
        print(f"lgcov judge: invalid config: {exc}", file=sys.stderr)
        return EXIT_ERROR
    gate = replace(cfg.gate, inconclusive_blocks=True) if args.fail_on_inconclusive else cfg.gate
    k = (cfg.samples_per_case or 1) if args.samples is None else args.samples
    taxonomy_path = args.taxonomy or cfg.taxonomy_path
    if k < 1 or not taxonomy_path:
        print(
            "lgcov judge: needs a taxonomy (--taxonomy or taxonomy_path) and k >= 1",
            file=sys.stderr,
        )
        return EXIT_ERROR
    try:
        every = list(load_taxonomy(taxonomy_path).kpi_specs)
        skipped = [s.name for s in every if s.judge == "llm" and _llm_judge is None]
        specs = [s for s in every if s.name not in skipped]
        judges = [_l2_judge(spec, cfg.jev_model) for spec in specs]
    except (OSError, ValueError) as exc:
        print(f"lgcov judge: invalid taxonomy: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if skipped:
        print("Skipped, no llm client: " + ", ".join(f"`{n}`" for n in skipped) + ".")
    try:
        advisory, flagged, calibration = _gate_bands(args.bands, every, replace(cfg, gate=gate))
    except (OSError, ValueError) as exc:
        print(f"lgcov judge: invalid config or bands.json: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        # Exception: importing user graph code can raise anything.
        graph = _load_graph(cfg.graph_entrypoint)
    except Exception as exc:
        return _harness_error("lgcov judge", "cannot load graph", exc)
    try:
        runs = _load_runs(str(Path(cfg.cassette_dir) / "baseline.json"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _harness_error("lgcov judge", "cannot read baseline", exc)
    try:
        replays = [replay_steps(graph, r, cfg.ignore_paths, serve=not args.live) for r in runs]
    except NodeError as exc:
        return _node_failure("lgcov judge", exc)
    except Exception as exc:  # replay itself, or a router / subgraph (not attributed yet, T109)
        return _harness_error("lgcov judge", "replay failed outside node code", exc)
    plan = _l2_plan(specs, runs, replays)
    saved = _saved_cases(Path(args.bands).parent / "cases", specs)
    total, model = _l2_calls(plan, k)
    total += sum(len(c) for c in saved.values())  # T102: each saved case, judged once more
    model += sum(len(saved.get(s.name, [])) for s in specs if s.judge != "code")
    print(f"Planned: {total} judge calls, {model} to model judges (--max-calls {args.max_calls})")
    if model > args.max_calls:
        print(
            f"lgcov judge: refusing to make {model} model judge calls (more than "
            f"--max-calls {args.max_calls}); lower --samples or raise --max-calls",
            file=sys.stderr,
        )
        return EXIT_ERROR
    stats = _l2_gate(plan, judges, k, gate, runs, replays, advisory, flagged)
    print("\n".join([*calibration, *([""] if calibration else [])]), end="")
    print(
        "\n".join([*render_gate_table(stats, gate.alpha, gate.power), "", *render_kpi_table(stats)])
    )
    rechecked = _rejudge(saved, specs, judges)
    if rechecked:
        print("\n".join(["", *rechecked]))
    return gate_exit_code(stats, gate.inconclusive_blocks)


_BANDS = "calibration/bands.json"  # T063: in the repo, so a band moves in a reviewed diff


def _pinned_jev_model(path: str) -> str | None:
    # calibrate reads only jev_model; it needs no graph, so a stand-in entrypoint validates
    if not Path(path).is_file():
        return None
    return load_config(path, {"graph_entrypoint": "calibrate.py:unused"}).jev_model


def _calibration_inputs(
    bands: Path, labels: list[str], overrides: str | None
) -> tuple[list[Path], Path | None]:
    """T102: the label files given, else calibration/labels/*.csv and
    calibration/overrides.jsonl, next to bands.json."""
    if labels:
        return [Path(p) for p in labels], Path(overrides) if overrides else None
    saved = Path(overrides) if overrides else bands.parent / "overrides.jsonl"
    return sorted((bands.parent / "labels").glob("*.csv")), saved if saved.is_file() else None


def _label_rows(
    labels: list[Path], overrides: Path | None, specs: list[KPISpec]
) -> tuple[list[Row], int]:
    score_maps = {spec.name: spec.score_map for spec in specs}
    rows = [row for path in labels for row in read_labels(path.read_text(encoding="utf-8"))]
    if overrides is None:
        return rows, 0
    lines = overrides.read_text(encoding="utf-8").splitlines()
    extra, skipped = rows_from_overrides(lines, score_maps)
    return rows + extra, skipped


def _bands_after(
    stats: list[JudgeStats], before: object, jev_model: str | None
) -> tuple[str, object]:
    # T102: the new bands.json, moved from `before` by the up-guard, as text and parsed
    previous, _ = recorded_bands(before)
    text = bands_document(stats, jev_model, previous)
    after: object = json.loads(text)
    return text, after


def _recalibrate(
    bands: Path,
    labels: list[Path],
    overrides: Path | None,
    specs: list[KPISpec],
    jev_model: str | None,
) -> int:
    """Calibrate from these labels and rewrite bands.json, saying which bands moved."""
    rows, skipped = _label_rows(labels, overrides, specs)
    if not rows:
        print(
            f"No calibration labels: {bands} unchanged. Add CSVs under "
            f"{bands.parent / 'labels'}, or override a verdict with `lgcov review override`."
        )
        return 0
    stats = calibrate(rows, {spec.name: spec.score_map for spec in specs})
    before: object = json.loads(bands.read_text(encoding="utf-8")) if bands.is_file() else None
    text, after = _bands_after(stats, before, jev_model)
    kinds = {spec.name: spec.judge for spec in specs}
    print("\n".join(render_calibration(stats, kinds, jev_model, recorded_bands(after)[0])))
    bands.parent.mkdir(parents=True, exist_ok=True)
    bands.write_text(text, encoding="utf-8")
    print(f"Wrote {bands}: one band per judge, for `lgcov judge` and `lgcov ci`.")
    for line in band_changes(before, after):
        print(line)
    if skipped:
        print(
            f"{skipped} override{'' if skipped == 1 else 's'} skipped: no kpi or judge_label "
            "(saved before T063), or a human score that isn't one of the KPI's labels."
        )
    return 0


def _calibrate_cmd(argv: list[str]) -> int:
    """T063, T102: each judge's agreement with human labels, and its band."""
    ap = argparse.ArgumentParser(prog="lgcov calibrate")
    ap.add_argument(
        "labels",
        nargs="*",
        help="CSVs: case_id, judge_id, judge_label, human_label[, judge_confidence, "
        "question_form]; default: calibration/labels/*.csv and calibration/overrides.jsonl",
    )
    ap.add_argument("--overrides", help="the overrides `lgcov review override` saved")
    ap.add_argument("--taxonomy", help="taxonomy.yaml with each KPI's labels (default pass/fail)")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="lgcov.yaml, for jev_model")
    ap.add_argument("--bands", default=_BANDS, help="write each judge's band here (T063)")
    args = ap.parse_args(argv)
    try:
        jev_model = _pinned_jev_model(args.config)
        specs = list(load_taxonomy(args.taxonomy).kpi_specs) if args.taxonomy else []
        labels, overrides = _calibration_inputs(Path(args.bands), args.labels, args.overrides)
        return _recalibrate(Path(args.bands), labels, overrides, specs, jev_model)
    except (OSError, ValueError) as exc:  # ValueError covers malformed JSON lines too
        print(f"lgcov calibrate: {exc}", file=sys.stderr)
        return EXIT_ERROR


_REVIEW_USAGE = (
    "usage: lgcov review override --kpi KPI --case-id CASE --judge-label LABEL "
    "--human-label LABEL --reviewer NAME [--note TEXT]"
)


def _review_cmd(argv: list[str]) -> int:
    """T102: override a judge's verdict on one case. The override is a label; when the
    human disagrees, the case is saved so the judge is re-checked on it on every PR; and
    the judge's band is recalculated."""
    if argv[:1] != ["override"]:
        print(_REVIEW_USAGE, file=sys.stderr)
        return EXIT_ERROR
    ap = argparse.ArgumentParser(prog="lgcov review override")
    ap.add_argument("--kpi", required=True, help="the KPI whose verdict this overrides")
    ap.add_argument("--case-id", required=True, help="the case, as reports name it: run-0/respond")
    ap.add_argument("--judge-label", required=True, help="what the judge said")
    ap.add_argument("--human-label", required=True, help="what it should have said")
    ap.add_argument("--reviewer", required=True)
    ap.add_argument("--note", default="", help="why; saved with the override")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="lgcov.yaml (optional)")
    ap.add_argument("--graph", help="<file.py>:<attr>; overrides graph_entrypoint")
    ap.add_argument("--cassette-dir", help="overrides cassette_dir")
    ap.add_argument("--taxonomy", help="taxonomy.yaml; overrides taxonomy_path")
    ap.add_argument("--bands", default=_BANDS, help="bands.json; overrides and cases go beside it")
    args = ap.parse_args(argv[1:])
    overrides = {"graph_entrypoint": args.graph, "cassette_dir": args.cassette_dir}
    try:
        cfg = load_config(args.config, overrides)
        taxonomy_path = args.taxonomy or cfg.taxonomy_path
        if not taxonomy_path:
            raise ValueError("needs a taxonomy (--taxonomy or taxonomy_path)")
        specs = list(load_taxonomy(taxonomy_path).kpi_specs)
        spec = next((s for s in specs if s.name == args.kpi), None)
        if spec is None:
            raise ValueError(f"no KPI '{args.kpi}' in {taxonomy_path}")
        if spec.judge == "code":
            raise ValueError(f"KPI '{spec.name}' has a code judge, which is deterministic")
        bad = sorted({args.judge_label, args.human_label} - set(spec.score_map))
        if bad:
            raise ValueError(f"labels {bad} not in KPI '{spec.name}': {sorted(spec.score_map)}")
        runs = _load_runs(str(Path(cfg.cassette_dir) / "baseline.json"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"lgcov review: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        # Exception: importing and running user graph code can raise anything.
        graph = _load_graph(cfg.graph_entrypoint)
        fresh = [replay_steps(graph, run, cfg.ignore_paths, serve=True)[1] for run in runs]
    except Exception as exc:
        return _harness_error("lgcov review", "cannot replay the graph", exc)
    cases = _l2_cases(spec, fresh)
    if args.case_id not in cases:
        print(f"lgcov review: no case '{args.case_id}'; there are {sorted(cases)}", file=sys.stderr)
        return EXIT_ERROR
    node_input, node_output = cases[args.case_id]
    bands = Path(args.bands)
    bands.parent.mkdir(parents=True, exist_ok=True)
    decision = ReviewDecision(
        node=spec.node,
        action="override",
        reviewer=args.reviewer,
        reason=args.note,
        label=spec.score_map[args.human_label],
        kpi=spec.name,
        judge_label=args.judge_label,
        case_id=args.case_id,
    )
    candidate = {spec.node: {"input": node_input, "output": node_output}}
    apply_decision(decision, {}, candidate, bands.parent / "overrides.jsonl")
    print(f"Saved the override to {bands.parent / 'overrides.jsonl'}.")
    if args.human_label != args.judge_label:  # the judge got it wrong: re-check it every PR
        saved = bands.parent / "cases" / f"{spec.name}.jsonl"
        saved.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "case_id": args.case_id,
            "kpi": spec.name,
            "node_input": node_input,
            "node_output": node_output,
            "judge_label": args.judge_label,
            "human_label": args.human_label,
            "reviewer": args.reviewer,
            "reason": args.note,
        }
        old = saved.read_text(encoding="utf-8") if saved.is_file() else ""
        saved.write_text(upsert_case(old, record), encoding="utf-8")
        print(f"Saved the case to {saved}: the judge is re-checked on it on every PR.")
    try:
        labels, saved_overrides = _calibration_inputs(bands, [], None)
        return _recalibrate(bands, labels, saved_overrides, specs, cfg.jev_model)
    except (OSError, ValueError) as exc:
        print(f"lgcov review: {exc}", file=sys.stderr)
        return EXIT_ERROR


def _saved_cases(folder: Path, specs: list[KPISpec]) -> dict[str, list[dict[str, object]]]:
    """T102: per KPI, the cases a human overrode its judge on, from calibration/cases/."""
    saved: dict[str, list[dict[str, object]]] = {}
    for spec in specs:
        path = folder / f"{spec.name}.jsonl"
        if path.is_file():
            lines = path.read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines if line.strip()]
            saved[spec.name] = [r for r in records if isinstance(r, dict)]
    return saved


def _rejudge(
    saved: dict[str, list[dict[str, object]]], specs: list[KPISpec], judges: list[Judge]
) -> list[str]:
    """T102: the judge, again, on each saved case; agreement with the human, never the gate."""
    lines = []
    for spec, judge in zip(specs, judges, strict=True):
        if spec.name not in saved:
            continue
        agreed = judged = 0
        for case in saved[spec.name]:
            node_input, node_output = case.get("node_input"), case.get("node_output")
            if not isinstance(node_input, dict) or not isinstance(node_output, dict):
                continue
            try:
                verdict = judge.judge(node_input, node_output)
            except (JudgeInfraError, *TRANSPORT_ERRORS):
                continue  # not judged: neither agrees nor disagrees
            judged += 1
            agreed += verdict.label == case.get("human_label")
        lines += saved_case_lines(spec.name, agreed, judged)
    return lines


def _tree(folder: Path) -> dict[str, bytes]:
    if not folder.is_dir():
        return {}
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def _calibration_changes(args: argparse.Namespace, specs: list[KPISpec], cfg: Config) -> list[str]:
    """T102: when the PR changed calibration/, the bands its labels earn, against main's."""
    here = Path(args.bands)
    if not args.base_dir or here.is_absolute():
        return []
    base = Path(args.base_dir) / here
    if _tree(here.parent) == _tree(base.parent):
        return []
    labels, overrides = _calibration_inputs(here, [], None)
    rows, _ = _label_rows(labels, overrides, specs)
    if not rows:
        return [f"This PR changed {here.parent}/ but it has no calibration labels."]
    before: object = json.loads(base.read_text(encoding="utf-8")) if base.is_file() else None
    stats = calibrate(rows, {spec.name: spec.score_map for spec in specs})
    _, after = _bands_after(stats, before, cfg.jev_model)
    lines = band_changes(before, after) or ["Calibration changed; no judge's band moved."]
    committed: object = json.loads(here.read_text(encoding="utf-8")) if here.is_file() else None
    if recorded_bands(committed) != recorded_bands(after):
        lines.append(f"⚠️ {here} doesn't match its labels: run `lgcov calibrate` and commit it.")
    return lines


@dataclass(frozen=True)
class _Expected:
    outcome: Outcome
    root: str | None  # the node lgcov must blame, if any
    fail_on: FailOn
    cassette_dir: str  # relative to the fixture folder


def _expected(path: Path) -> _Expected:
    raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("must be a mapping")
    names = {o.name: o for o in Outcome}
    outcome, root = raw.get("outcome"), raw.get("root")
    if outcome not in names:
        raise ValueError(f"outcome must be one of {sorted(names)}, got {outcome!r}")
    if root is not None and not isinstance(root, str):
        raise ValueError("root must be a node name")
    return _Expected(
        outcome=names[str(outcome)],
        root=root,
        fail_on=parse_fail_on(str(raw.get("fail_on", "root-drift"))),
        cassette_dir=str(raw.get("cassette_dir", "../cassettes")),
    )


def _selfcheck_cmd(argv: list[str]) -> int:
    """T074: run the harness on fixtures whose outcome is known; a mismatch is a lgcov bug."""
    ap = argparse.ArgumentParser(prog="lgcov selfcheck")
    ap.add_argument("--dir", default="tests/fixtures/selfcheck", help="fixture folders")
    args = ap.parse_args(argv)
    fixtures = sorted(p.parent for p in Path(args.dir).glob("*/expected.yaml"))
    if not fixtures:
        print(f"lgcov selfcheck: no fixtures (*/expected.yaml) under {args.dir}", file=sys.stderr)
        return EXIT_ERROR
    rows, all_ok = [], True
    for fixture in fixtures:
        try:
            want = _expected(fixture / "expected.yaml")
        except (OSError, ValueError) as exc:
            print(f"lgcov selfcheck: {fixture.name}/expected.yaml: {exc}", file=sys.stderr)
            return EXIT_ERROR
        # The same path the GitHub Action takes (`lgcov ci`), on this fixture alone.
        result = _ci_result(
            argparse.Namespace(
                fail_on=want.fail_on,
                harness_error=None,
                graph=f"{fixture / 'graph.py'}:graph",
                cassette_dir=str(fixture / want.cassette_dir),
                config=str(fixture / "lgcov.yaml"),  # usually absent: defaults
                taxonomy=None,
                base_dir=None,
                live=False,
                coverage_out=None,
                coverage_baseline=None,
                commit="",
                max_calls=200,
                fail_on_inconclusive=False,
                on_divergence="flag",
                budget_seconds=None,
                tier=None,
                nightly_url=None,
                bands="calibration/bands.json",
            )
        )
        actual = Outcome(result.exit_code)
        ok = actual == want.outcome and (want.root is None or want.root in result.root_drift)
        all_ok = all_ok and ok
        expected = want.outcome.name + (f" @ {want.root}" if want.root else "")
        root = ", ".join(result.root_drift) or "—"
        rows.append(
            f"| {fixture.name} | {expected} | {actual.name} | {root} | {'✓' if ok else '✗'} |"
        )
    print(
        "\n".join(["| fixture | expected | actual | root | ok |", "|---|---|---|---|---|", *rows])
    )
    return 0 if all_ok else 1


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
    if argv[:1] == ["selfcheck"]:
        return _selfcheck_cmd(argv[1:])
    if argv[:1] == ["judge"]:
        return _judge_cmd(argv[1:])
    if argv[:1] == ["calibrate"]:
        return _calibrate_cmd(argv[1:])
    if argv[:1] == ["review"]:
        return _review_cmd(argv[1:])
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
