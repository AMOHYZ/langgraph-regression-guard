"""graph-diff CLI: print node/edge changes between two graph snapshots."""

import argparse
import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from types import CodeType, ModuleType
from typing import Any

import yaml

from lgcov.attribution import (
    ROUTING,
    Benchmark,
    NodeLabels,
    Probe,
    Result,
    Score,
    classify,
    explain_runs,
    parse_benchmark,
    pending_result,
    render_markdown,
    results_json,
    score,
)
from lgcov.bench_round import check as check_round
from lgcov.bench_round import (
    exposures,
    ledger_lines,
    lock_round,
    new_round,
    parse_ledger,
    parse_round,
    round_to_yaml,
    tree_state,
)
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
from lgcov.candidate import CandidateRun, FreshRun, candidate_to_json, parse_candidate
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
    changed_nodes,
    compared_run,
    judge_probes,
    probe_steps,
    record,
    replay_run,
    runs_from_json,
    runs_to_json,
    unservable,
)
from lgcov.ci_report import CiResult, Tally, Timing, render_ci_report
from lgcov.code_judges import code_judge
from lgcov.config import DEFAULT_PATH as DEFAULT_CONFIG
from lgcov.config import (
    TIERS,
    Config,
    GateConfig,
    LangSmithConfig,
    Tier,
    TierPolicy,
    load_config,
    tier_policy,
)
from lgcov.coverage import (
    CoverageSnapshot,
    coverage_delta,
    format_coverage_line,
    node_floor_miss,
    snapshot_coverage,
)
from lgcov.draft import Drafter, approve, as_doc, draft, parse_traces, to_yaml
from lgcov.drift_gate import FailOn, Outcome, drift_exit_code, parse_fail_on, verdict
from lgcov.gate_result import GateResult, file_hash, parse_result, result_to_json, verify_result
from lgcov.graph_diff import GraphDiff, diff_graphs
from lgcov.jev_judge import JevJudge
from lgcov.judges import KPI, FakeJudge, Judge, JudgeInfraError, NodeState
from lgcov.langsmith import (
    GetClient,
    LangSmithError,
    cases,
    experiment_url,
    from_env,
    matching,
)
from lgcov.lint import lint, routers_of, summary
from lgcov.lock import Commits, locked_line, moved
from lgcov.messages import plain, run_tools
from lgcov.metrics import format_summary, parse_records, summarize
from lgcov.normalize import normalize
from lgcov.review import ReviewDecision, apply_decision
from lgcov.sampling import (
    KpiStats,
    gate_exit_code,
    gate_kpis,
    judge_samples,
    kpi_stats,
    named_cases,
    render_gate_table,
    render_kpi_table,
)
from lgcov.slices import Group, group_kpis, groups_exit, render_groups
from lgcov.snapshot import Edge, GraphSnapshot, snapshot_from_compiled
from lgcov.synthetic import Agent, Case, aa_arms, every_case, noise_rule_rates
from lgcov.synthetic_score import build_report, render_section, replace_section, report_json
from lgcov.taxonomy import KPISpec, PendingReview, load_taxonomy
from lgcov.verdict_actions import CI_STATUSES, MODES, CodeFailure, gate_findings

# 0 = no change / accepted, 1 = graph/replay changed or named node not drifted,
# 2 = bad input (missing/unreadable/invalid).
EXIT_ERROR = 2
EXIT_NOT_DRIFTED = 1
# T166: a side of `lgcov bench` couldn't load its graph (lgcov.bench_side).
CANT_IMPORT = 3


def _output_hash(step: Step) -> str:
    # T168: where a Command sent the run counts as much as its update
    output = step.output if step.goto is None else {"goto": step.goto, "update": step.output}
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
    except PendingReview as exc:  # T106: valid, but a human hasn't approved all of it yet
        print(f"lgcov taxonomy: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"lgcov taxonomy: invalid taxonomy: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"{tax.system}: {len(tax.kpi_specs)} KPIs")
    print("| name | node | judge | labels |")
    print("|---|---|---|---|")
    for spec in tax.kpi_specs:
        print(f"| {spec.name} | {spec.node} | {spec.judge} | {', '.join(spec.labels)} |")
    return 0


# T106: the client that drafts taxonomies. None is built in: `--client <file.py>:<attr>` (a
# class or function that makes one) or code that sets this provides it.
_drafter: Drafter | None = None


def _load_drafter(target: str) -> Drafter:
    path, sep, attr = target.rpartition(":")
    if not sep or not path or not attr:
        raise ValueError(f"client must be <file.py>:<attr>, got {target!r}")
    make = getattr(_import_file(path), attr, None)
    client = make() if callable(make) else None
    if not isinstance(client, Drafter):
        raise ValueError(f"{target} makes no drafting client (one with a propose method)")
    return client


def _taxonomy_draft(argv: list[str]) -> int:
    """T106: `lgcov taxonomy draft`: failure modes and KPIs proposed from traces, each one
    pending human review. It never approves; re-run, it adds only new failure signatures."""
    ap = argparse.ArgumentParser(prog="lgcov taxonomy draft")
    ap.add_argument("--description", required=True, help="what the system does (Markdown)")
    ap.add_argument("--traces", nargs="+", required=True, help="trace files (JSON)")
    ap.add_argument("--out", default="docs/taxonomy.draft.yaml", help="kept and added to")
    ap.add_argument("--system", default="agent", help="the system's name, for a new draft")
    ap.add_argument("--client", help="<file.py>:<attr>, a class or function that makes one")
    args = ap.parse_args(argv)
    out = Path(args.out)
    try:
        # Exception: the client is user code (an LLM call) and can raise anything.
        client = _load_drafter(args.client) if args.client else _drafter
        if client is None:
            print(
                "lgcov taxonomy draft: no drafting client (lgcov ships none): pass "
                "--client <file.py>:<attr>, a class or function that makes one",
                file=sys.stderr,
            )
            return EXIT_ERROR
        description = Path(args.description).read_text(encoding="utf-8")
        traces = [
            t for f in args.traces for t in parse_traces(json.loads(Path(f).read_text("utf-8")), f)
        ]
        existing = (
            as_doc(yaml.safe_load(out.read_text("utf-8")), str(out)) if out.is_file() else None
        )
        doc = draft(existing, args.system, description, traces, client)
    except Exception as exc:
        print(f"lgcov taxonomy draft: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    added = [
        str(m["id"])
        for m in doc["failure_modes"][len(existing["failure_modes"]) if existing else 0 :]
    ]
    if not added:
        print(f"No new failure signatures in the traces: {out} unchanged.")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_yaml(doc), encoding="utf-8")
    modes = f"{len(added)} failure mode{'' if len(added) == 1 else 's'}"
    print(
        f"{modes} drafted, pending human review, in {out}: {', '.join(added)}. "
        "Approve each with `lgcov taxonomy approve <id> --reviewer <name>`."
    )
    return 0


def _taxonomy_approve(argv: list[str]) -> int:
    """T106: a human's yes to one drafted entry: a failure mode id (with its KPIs) or a KPI."""
    ap = argparse.ArgumentParser(prog="lgcov taxonomy approve")
    ap.add_argument("entry", help="a failure mode id, or a KPI name")
    ap.add_argument("--reviewer", required=True, help="who approves it")
    ap.add_argument("--file", default="docs/taxonomy.draft.yaml", help="the draft")
    args = ap.parse_args(argv)
    path = Path(args.file)
    try:
        doc = as_doc(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))
        doc = approve(doc, args.entry, args.reviewer)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"lgcov taxonomy approve: {exc}", file=sys.stderr)
        return EXIT_ERROR
    path.write_text(to_yaml(doc), encoding="utf-8")
    left = sum(e.get("review") == "pending" for e in [*doc["failure_modes"], *doc["kpis"]])
    print(f"Approved `{args.entry}` as {args.reviewer}; {left} entries still pending review.")
    return 0


class _SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    """Always compile from source, never from __pycache__: a .pyc is reused when the
    source keeps its size and second-resolution mtime, which a quick same-length edit
    (A1 -> A2, a prompt tweak) does, and replay would then run the old graph.
    """

    def get_code(self, fullname: str) -> CodeType:
        return self.source_to_code(self.get_data(self.path), self.path)


def _hides_a_module(name: str, path: str) -> bool:
    """Would the file, registered as `name`, hide a real module from every import of it, the
    file's own too? Finding itself, or a graph lgcov loaded earlier, hides nothing."""
    try:
        found = importlib.util.find_spec(name)
    except ImportError:  # a dotted stem with no such parent
        return False
    except ValueError:  # in sys.modules with no spec, as __main__ can be
        return True
    if found is None or isinstance(found.loader, _SourceOnlyLoader):
        return False
    return found.origin is None or Path(found.origin).resolve() != Path(path).resolve()


def _import_file(path: str, name: str | None = None) -> ModuleType:
    """Import a user's file and keep it in sys.modules, as an import does: the standard library,
    pydantic and LangGraph look a class's module up there, while loading and while a node runs.

    The name is the file's stem unless a real module has it. It stays the same from one run to
    the next because fingerprints can include it (a pydantic ref, a dataclass's methods).
    """
    if name is None:
        stem = Path(path).stem
        name = f"lgcov_user_{stem}" if _hides_a_module(stem, path) else stem
    spec = importlib.util.spec_from_file_location(name, path, loader=_SourceOnlyLoader(name, path))
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Any: a compiled graph from user code; see cassette.record for why it stays untyped.
def _load_graph(target: str, name: str | None = None) -> Any:
    path, sep, attr = target.rpartition(":")
    if not sep or not path or not attr:
        raise ValueError(f"target must be <file.py>:<attr>, got {target!r}")
    graph = getattr(_import_file(path, name), attr, None)
    if not hasattr(graph, "stream"):
        raise ValueError(f"{target} is not a compiled graph")
    return graph


def _load_runs(path: str) -> list[Cassette]:
    return runs_from_json(Path(path).read_text(encoding="utf-8"))


def _replay_under(
    cfg: Config,
    graph: Any,
    run: Cassette,
    serve: bool,
    on_divergence: OnDivergence = "flag",
    budget: Budget | None = None,
) -> Replay:
    """`replay_run` with what lgcov.yaml says to mask."""
    return replay_run(
        graph,
        run,
        cfg.ignore_paths,
        serve,
        on_divergence,
        budget,
        mask_volatile=cfg.mask_volatile_in_key,
    )


def _drift_kinds(
    runs: list[Cassette], drifts: list[list[Drift]]
) -> tuple[dict[str, int], set[str]]:
    """Runs each node drifted in, and the root nodes. "root" = the first node to drift
    in some run, in execution order; "propagated" nodes drifted only after an earlier one.
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
    runs: list[Cassette],
    drifts: list[list[Drift]],
    not_checked: tuple[str, ...] = (),
    labels: NodeLabels | None = None,
) -> list[str]:
    """Per-node drift table: root or propagated, and in how many runs. T162: with `labels`, root,
    propagated or inconclusive by them; without, root is the first node to drift in some run."""
    counts, roots = _drift_kinds(runs, drifts)
    roots = roots if labels is None else set(labels.root)
    unsure = {} if labels is None else labels.inconclusive
    routing = {} if labels is None else labels.routing  # T175: root, though its output held
    lines = ["### Cassette replay", ""]
    if not counts:
        return [*lines, f"No cassette drift ({len(runs)} runs)."]
    lines += ["| node | drift | runs |", "|---|---|---|"]
    for node in sorted({*counts, *routing}):
        kind = "root" if node in roots else "propagated"
        kind = f"inconclusive ({unsure[node]})" if node in unsure else kind
        kind = f"root ({ROUTING.format(node)})" if node in routing else kind
        lines.append(f"| `{node}` | {kind} | {routing.get(node, counts.get(node))}/{len(runs)} |")
    drifted = {*counts, *routing, *not_checked}
    unchanged = sorted({s.node for r in runs for s in r.steps} - drifted)
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


def _rebind_lines(replays: list[Replay]) -> list[str]:
    """T165: the steps that ran live because their recorded ids couldn't be fitted to this run's."""
    return [
        f"rebind_skipped: {node} (run {i}): its recorded ids don't map one-to-one onto this "
        "run's, so it ran live."
        for i, r in enumerate(replays, 1)
        for node in r.rebind_skipped
    ]


def _raised_lines(replays: list[Replay]) -> list[str]:
    """T152: the calls that raised while the run went on. They leave no step, so nothing else
    shows that a write in one may have happened as well as the one in the call that succeeded."""
    raised = Counter((i, node) for i, r in enumerate(replays, 1) for node in r.failed_attempts)
    if not raised:
        return []
    where = ", ".join(f"`{node}` in run {i} ({n}x)" for (i, node), n in raised.items())
    return [
        f"Raised, and the run went on (a retry policy or error handler): {where}. "
        "What a failed attempt did before raising, like a write, still happened."
    ]


def _say_replay_notes(replays: list[Replay]) -> None:
    for line in [*_rebind_lines(replays), *_raised_lines(replays)]:
        print(line, file=sys.stderr)


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
        where = f"harness error (transport) in node `{exc.node}`, not a regression"
        print(f"{prog}: {where}: {exc.raised}", file=sys.stderr)
        return Outcome.INFRA_ERROR
    print(f"{prog}: agent failure: {exc}")
    return Outcome.AGENT_FAIL


_LIVE_HELP = "re-run every node; by default a node whose code and input match is served"
_INCONCLUSIVE_HELP = "exit 1 on an INCONCLUSIVE blocking KPI; overrides gate.inconclusive_blocks"
_MODE_HELP = "advisory: report only; only a harness error fails the check"  # T153
_APPROVED_BY_HELP = "who approved this PR, comma-separated; recorded, it changes no verdict"
_CI_STATUS_HELP = "the result of your other CI jobs; shown, it changes no verdict"


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
        replays = [_replay_under(cfg, graph, run, serve=not args.live) for run in runs]
    except NodeError as exc:
        return _node_failure("lgcov replay", exc)
    except Exception as exc:  # replay itself: no node or router raised it
        return _harness_error("lgcov replay", "replay failed outside node code", exc)
    drifts = [list(r.drifts) for r in replays]
    skipped = _rebind_lines(replays)
    notes = [*skipped, *_raised_lines(replays)]
    print("\n".join([*_replay_markdown(runs, drifts), *([""] if notes else []), *notes]))
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
        if skipped:
            summary["rebind_skipped"] = sorted({n for r in replays for n in r.rebind_skipped})
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
    path.write_text(runs_to_json(runs), encoding="utf-8")


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


# T103: the LangSmith client; tests swap in a fake. None: an httpx.Client per request.
_langsmith_client: GetClient | None = None


def _import_langsmith(argv: list[str]) -> int:
    """T103: a LangSmith dataset's examples as lgcov cases, read-only."""
    ap = argparse.ArgumentParser(prog="lgcov import langsmith")
    ap.add_argument("--dataset", required=True, help="the LangSmith dataset's name")
    ap.add_argument("--out", default="cases.json", help="where the cases go (cases.json)")
    ap.add_argument("--limit", type=int, help="import at most this many examples")
    ap.add_argument("--split", help="import only this split of the dataset")
    args = ap.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        ap.error("--limit must be at least 1")
    ls = from_env(_langsmith_client)
    if ls is None:
        print(
            "lgcov import langsmith: LANGSMITH_API_KEY isn't set; nothing imported",
            file=sys.stderr,
        )
        return EXIT_ERROR
    try:
        dataset_id = ls.dataset_id(args.dataset)
        inputs, sidecar = cases(ls.examples(dataset_id, args.limit, args.split))
    except LangSmithError as exc:
        print(f"lgcov import langsmith: {exc}", file=sys.stderr)
        return EXIT_ERROR
    out = Path(args.out)
    beside = out.with_suffix(".langsmith.json")  # example ids and outputs, in the same order
    out.parent.mkdir(parents=True, exist_ok=True)
    for path, body in ((out, inputs), (beside, sidecar)):
        path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"Imported {len(inputs)} cases from LangSmith dataset `{args.dataset}` to {out} "
        f"(ids and outputs in {beside}); record them with `lgcov record --inputs {out}`"
    )
    return 0


def _pr_head() -> str | None:
    """T103: the PR's head commit, from the pull_request event GitHub hands the job."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    try:
        event: object = json.loads(Path(path).read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        return None
    pr = event.get("pull_request") if isinstance(event, dict) else None
    head = pr.get("head") if isinstance(pr, dict) else None
    sha = head.get("sha") if isinstance(head, dict) else None
    return sha if isinstance(sha, str) else None


def _langsmith_notes(cfg: LangSmithConfig, commit: str) -> list[str]:
    """T103: the link to the experiment on lgcov.yaml's dataset that ran on the commit this
    run tested or on the PR's head. Nothing without a dataset or with link: false; a
    missing key or a LangSmith problem is a note, never a failure."""
    if not cfg.dataset or not cfg.link:
        return []
    ls = from_env(_langsmith_client)
    if ls is None:
        return [
            "LangSmith: LANGSMITH_API_KEY isn't set (fork PRs get no secrets); no experiment "
            f"link for `{cfg.dataset}`."
        ]
    commits = {c for c in (commit, _head("."), _pr_head()) if c}
    try:
        found = matching(ls.experiments(ls.dataset_id(cfg.dataset)), commits)
    except LangSmithError as exc:
        return [f"⚠️ {exc}; no experiment link."]
    if found is None:
        return []
    url = experiment_url(ls.url, found)
    return [f"🔗 LangSmith experiment for this commit: [{found.get('name')}]({url})"]


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
                    old_hashes.append(_output_hash(step))
            for step in neu.steps:
                if step.node == node:
                    new_hashes.append(_output_hash(step))
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
                for node in accept_nodes(base, neu, {s.node for s in base.steps}, cfg.ignore_paths)[
                    1
                ]
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
        results = [
            accept_nodes(b, f, nodes, cfg.ignore_paths) for b, f in zip(runs, fresh, strict=True)
        ]
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
        # A name of its own: head runs after this, and must still find head in sys.modules.
        base = snapshot_from_compiled(_load_graph(base_target, "lgcov_base_graph"))
    except Exception as exc:
        return [], f"Could not load the base graph ({_harness_message('load', exc)}); skipped."
    return _graph_diff_lines(diff_graphs(base, head)), ""


def _policy_path(args: argparse.Namespace, path: str) -> str:
    """T154: where the gate reads a policy file: in the base checkout when there is one, so a
    PR's edit to the file can't change what grades it."""
    if not args.policy_dir:
        return path
    file = Path(path)
    if file.is_absolute():
        try:
            file = file.relative_to(Path.cwd())
        except ValueError:
            return path  # outside the PR's checkout
    return str(Path(args.policy_dir) / file)


def _first_adoption(args: argparse.Namespace) -> bool:
    """T154: nothing on the base says what to enforce yet: no lgcov.yaml, and no gate through
    the Action's inputs either (a graph input and a recorded baseline)."""
    if not args.policy_dir or Path(_policy_path(args, args.config)).is_file():
        return False
    baseline = Path(_policy_path(args, args.cassette_dir or "cassettes")) / "baseline.json"
    return not (args.graph and baseline.is_file())


def _moved_graph(args: argparse.Namespace, cfg: Config) -> str:
    """T154: why a graph this PR moved won't load: the base's lgcov.yaml names the old path."""
    if not args.policy_dir or args.graph or Path(cfg.graph_entrypoint.rpartition(":")[0]).exists():
        return ""
    return (
        f"\nlgcov.yaml on the base names `{cfg.graph_entrypoint}`, and this PR has no such file. "
        "The gate reads lgcov.yaml from the base, so land the new path first, or keep a shim "
        "at the old one."
    )


def _cassettes_edited(args: argparse.Namespace, cfg: Config) -> bool:
    """T154: this PR changed the expected outputs it is graded on. The gate still uses them,
    or `cassette accept` could never pass in its own PR."""
    base = _policy_path(args, cfg.cassette_dir)
    return bool(args.policy_dir) and _tree(Path(cfg.cassette_dir)) != _tree(Path(base))


def _policy_files(args: argparse.Namespace) -> list[str]:
    return [args.config, *([args.taxonomy] if args.taxonomy else []), args.bands]


def _bytes(path: str) -> bytes | None:
    return Path(path).read_bytes() if Path(path).is_file() else None


def _policy_hashes(args: argparse.Namespace) -> dict[str, str]:
    """T154: each policy file's hash, from where the gate reads it."""
    return {f: file_hash(_bytes(_policy_path(args, f))) for f in _policy_files(args)}


def _policy_notes(args: argparse.Namespace, cfg: Config, cassettes_edited: bool) -> list[str]:
    """T154: what this PR edited of the gate's own inputs, for the report."""
    if not args.policy_dir:
        return []
    edited = [f for f in _policy_files(args) if _bytes(f) != _bytes(_policy_path(args, f))]
    notes = []
    if edited:
        notes.append(
            f"{_quoted(edited)}: read from the base commit, so this PR's edits apply after "
            "it merges."
        )
    if cassettes_edited:
        notes.append(
            f"⚠️ This PR changes `{cfg.cassette_dir}`, the expected outputs this gate compares "
            "against, and the gate used its version. Check that diff."
        )
    return notes


@dataclass(frozen=True)
class _GatePolicy:
    """T179: what a gate run reads before either half of it starts, from the base on a PR."""

    cfg: Config
    tier: Tier
    rules: TierPolicy
    budget: float | None  # seconds for live node calls
    l2: tuple[list[KPISpec], list[Judge]] | None = None
    advisory: frozenset[str] = frozenset()
    flagged: frozenset[str] = frozenset()
    llm_code: int = 0
    llm_notes: tuple[str, ...] = ()
    calibration: tuple[str, ...] = ()
    kpi_nodes: frozenset[str] | None = None  # T104: for lint; None: no taxonomy, no check


def _gate_policy(args: argparse.Namespace) -> _GatePolicy | str:
    """lgcov.yaml, the taxonomy and the bands; a string says why they can't be read."""
    overrides = {"graph_entrypoint": args.graph, "cassette_dir": args.cassette_dir}
    # T154: a fix or a new file in this PR can't help until it merges, so a broken policy says so.
    from_base = (
        "\nThis is read from the base commit, so this PR's edits apply after it merges."
        if args.policy_dir
        else ""
    )
    try:
        cfg = load_config(_policy_path(args, args.config), overrides)
    except (OSError, ValueError) as exc:
        return f"invalid lgcov.yaml: {exc}{from_base}"
    tier: Tier = args.tier or cfg.tier
    rules = tier_policy(tier, cfg.samples_per_case)
    budget = rules.budget if args.budget_seconds is None else args.budget_seconds
    if not args.taxonomy:
        return _GatePolicy(cfg, tier, rules, budget)
    try:
        every = list(load_taxonomy(_policy_path(args, args.taxonomy)).kpi_specs)
        llm = [s.name for s in every if s.judge == "llm"]
        runs_llm = rules.llm_judges and _llm_judge is not None
        specs = every if runs_llm else [s for s in every if s.judge != "llm"]
        l2 = (specs, [_l2_judge(spec, cfg.jev_model, cfg.jev_timeout_s) for spec in specs])
    except (OSError, ValueError) as exc:
        return f"invalid taxonomy: {exc}{from_base}"
    try:
        advisory, flagged, calibration = _gate_bands(_policy_path(args, args.bands), every, cfg)
    except (OSError, ValueError) as exc:
        return f"invalid lgcov.yaml or bands.json: {exc}{from_base}"
    llm_notes, llm_code = _llm_notes(llm, advisory, rules.llm_judges)
    return _GatePolicy(
        cfg,
        tier,
        rules,
        budget,
        l2,
        advisory,
        flagged,
        llm_code,
        tuple(llm_notes),
        tuple(calibration),
        frozenset(s.node for s in every),
    )


def _run_candidate(args: argparse.Namespace, policy: _GatePolicy, budget: Budget) -> CandidateRun:
    """T179: the half of a gate run that runs the PR's code: it loads the graph and replays the
    runs. Nothing here judges, compares or decides."""
    cfg = policy.cfg
    budget.seconds = policy.budget
    try:
        # Exception: importing user graph code can raise anything.
        graph = _load_graph(cfg.graph_entrypoint)
        head = snapshot_from_compiled(graph)
    except Exception as exc:
        msg = _harness_message("cannot load graph", exc) + _moved_graph(args, cfg)
        return CandidateRun(harness_error=msg)
    graph_lines, graph_note = _base_diff(args.base_dir, cfg.graph_entrypoint, head)
    run = CandidateRun(
        tier=policy.tier, head=head, graph_lines=tuple(graph_lines), graph_note=graph_note
    )
    try:
        runs = _load_runs(str(Path(cfg.cassette_dir) / "baseline.json"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return replace(run, harness_error=_harness_message("cannot read baseline", exc))
    kpi_nodes = None if policy.kpi_nodes is None else set(policy.kpi_nodes)
    found = lint(head, routers_of(graph), {s.node for r in runs for s in r.steps}, kpi_nodes)
    run = replace(  # T104: before replay; warnings only, it never blocks
        run, lint_summary=summary(found), lint_lines=tuple(f.line() for f in found)
    )
    serve = policy.rules.serve and not args.live
    budget.expected_live = unservable(graph, runs) if serve else sum(len(r.steps) for r in runs)
    agent_start = perf_counter()
    try:
        replays = [
            _replay_under(cfg, graph, recorded, serve, args.on_divergence, budget)
            for recorded in runs
        ]
    except NodeError as exc:
        original = exc.raised
        if exc.transport:
            return replace(run, harness_error=original, harness_node=exc.node)
        return replace(run, crash=f"Node `{exc.node}` raised {original}", crash_node=exc.node)
    except Exception as exc:  # replay itself: no node or router raised it
        return replace(run, harness_error=_harness_message("replay failed outside node code", exc))
    probes = probe_steps(graph, runs, changed_nodes(graph, runs))  # T162: offline, no verdict
    return replace(
        run,
        fresh=tuple(FreshRun(r.steps, r.calls, r.halt) for r in replays),
        probes=probes,
        agent_seconds=perf_counter() - agent_start,
        budget_hit=budget.hit,
    )


def _evaluate(
    args: argparse.Namespace, policy: _GatePolicy, run: CandidateRun, start: float
) -> CiResult:
    """T179: the half of a gate run that decides. It only reads: what the other half ran, the
    PR's cassette and the base's policy. So it needs no project install, and with the other half
    in another job, the PR's code never runs where this does."""
    fail_on: FailOn = args.fail_on
    cfg, l2 = policy.cfg, policy.l2
    if run.head is None:
        return CiResult(fail_on, harness_error=run.harness_error)
    edited = _cassettes_edited(args, cfg)
    partial = CiResult(
        fail_on,
        run.graph_lines,
        run.graph_note,
        cassettes_edited=edited,
        lint_summary=run.lint_summary,
        lint_lines=run.lint_lines,
    )
    if run.harness_error:
        return replace(partial, harness_error=run.harness_error, harness_node=run.harness_node)
    if run.crash:
        return replace(partial, crash=run.crash, root_drift=(run.crash_node,))
    try:
        runs = _load_runs(str(Path(cfg.cassette_dir) / "baseline.json"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return replace(partial, harness_error=_harness_message("cannot read baseline", exc))
    if len(run.fresh) != len(runs):
        msg = (
            "The candidate job's result doesn't match this PR's cassette: "
            f"{len(run.fresh)} runs replayed, {len(runs)} recorded."
        )
        return replace(partial, harness_error=msg)
    replays = [
        compared_run(recorded, fresh.steps, fresh.calls, fresh.halt, cfg.ignore_paths)
        for recorded, fresh in zip(runs, run.fresh, strict=True)
    ]
    pairs = [(list(r.drifts), list(r.steps)) for r in replays]
    drifts = [d for d, _ in pairs]
    try:
        probes = judge_probes(runs, run.probes, cfg.ignore_paths)
    except ValueError as exc:
        return replace(partial, harness_error=_harness_message("cannot use the probes", exc))
    # T162: root by fingerprint and probe. Under fail-on: root-drift an inconclusive node blocks
    # with the roots, since a live model node can't be probed until T211.
    counts, _ = _drift_kinds(runs, drifts)
    bases = {s.node: s.code for r in runs for s in r.steps}
    heads = {s.node: s.code for f in run.fresh for s in f.steps}
    firsts = [_run_root(r, d) for r, d in zip(runs, drifts, strict=True)]
    nodes = [{x.node for x in d} for d in drifts]
    # T175: a changed router makes its source node root. Old recordings have no router print.
    routers = (
        {s.node: s.router for r in runs for s in r.steps},
        {s.node: s.router for f in run.fresh for s in f.steps},
    )
    ran = [[s.node for s in r.steps] for r in runs]
    by_node = classify(counts.keys(), (bases, heads), probes)
    labels = explain_runs(by_node, firsts, nodes, routers, ran)
    blocking = {*labels.root, *labels.inconclusive}
    root, downstream = sorted(blocking), list(labels.propagated)
    # T199: the comment's tally line: each run's root and inconclusive nodes, in run order.
    in_order = [s.node for r, n in zip(runs, nodes, strict=True) for s in r.steps if s.node in n]
    named = dict.fromkeys(n for n in [*in_order, *root] if n in blocking)
    tally = Tally(len(runs), sum(1 for d in drifts if d), tuple(named))
    # T083: coverage of the runs replay just did; nothing executes again for it.
    cov = snapshot_coverage(run.head, [[s.node for s in steps] for _, steps in pairs], args.commit)
    if args.coverage_out:
        Path(args.coverage_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.coverage_out).write_text(cov.to_json(), encoding="utf-8")
    base, note = _coverage_baseline(args.coverage_baseline)
    # T089: nodes a run stopped before (divergence or budget) are INCONCLUSIVE, never a fail.
    not_checked = tuple(dict.fromkeys(n for r in replays for n in r.not_checked))
    under_floor = node_floor_miss(cov, cfg.thresholds.min_node_coverage)
    diverged = [
        *_divergence_lines(replays, args.on_divergence),
        *_rebind_lines(replays),
        *_raised_lines(replays),
    ]
    calls = [c for r in replays for c in r.calls]
    timing = Timing(
        0.0,
        run.agent_seconds,
        live=sum(1 for c in calls if c.how != "served"),
        served=sum(1 for c in calls if c.how == "served"),
        budget=policy.budget,
        budget_hit=run.budget_hit,
    )
    base_runs, base_notes = _base_runs(args, cfg) if edited else (list[Cassette](), list[str]())
    paired, removed = _pair_with_base(runs, base_runs, cfg.ignore_paths)
    result = replace(
        partial,
        inconclusive_blocks=args.fail_on_inconclusive or cfg.gate.inconclusive_blocks,
        replay_lines=(
            *_replay_markdown(runs, drifts, not_checked, labels),
            *([""] if diverged else []),
            *diverged,
        ),
        root_drift=tuple(root),
        propagated_drift=tuple(downstream),
        first_drift=tuple(n for n in dict.fromkeys(firsts) if n),
        inconclusive=tuple(sorted(labels.inconclusive)),
        tally=tally,
        coverage_line=format_coverage_line(cov, base),
        newly_uncovered=coverage_delta(base, cov).newly_uncovered if base else (),
        under_floor=under_floor,
        notes=(
            *_policy_notes(args, cfg, edited),
            *base_notes,
            *_removed_note(removed, cfg.cassette_dir),
            *policy.llm_notes,
            *_whole_run_note(l2[0] if l2 else []),
            *([note] if note else []),
            *([f"Latest nightly run on main: {args.nightly_url}"] if args.nightly_url else []),
            *_langsmith_notes(cfg.langsmith, args.commit),
        ),
        tier=policy.tier,
        not_checked=not_checked,
        timing=replace(timing, total=run.seconds + perf_counter() - start),
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
        saved = _saved_cases(Path(_policy_path(args, args.bands)).parent / "cases", l2[0])
        extra = sum(len(saved.get(s.name, [])) for s in l2[0] if s.judge != "code")
        stopped = frozenset(i for i, r in enumerate(replays) if r.not_checked)
        stats, gate = _ci_gate(
            args,
            cfg,
            policy.rules.k,
            policy.advisory,
            policy.flagged,
            *l2,
            runs,
            pairs,
            extra,
            stopped,
        )
    except Exception as exc:  # a judge's own bug or the call limit: harness, not the PR's graph
        msg = _harness_message("L2 gate failed", exc)
        return replace(result, harness_error=msg)
    tokens = [s.tokens_total for s in stats if s.tokens_total]  # code judges report 0
    judged = replace(
        timing,
        judges=perf_counter() - judge_start,
        judge_calls=sum(s.samples for s in stats),
        tokens=sum(tokens) if tokens else None,
    )
    groups = _groups(stats, l2[0], cfg, gate)
    findings = gate_findings(stats, groups)
    hidden = _hidden_failures(*l2, stats, [steps for _, steps in pairs], paired, stopped)
    worse = {s.kpi: {*s.worse} for s in stats}
    for failure in hidden:  # a re-baseline can't hide a run from the tally either
        worse[failure.kpi] |= {*failure.cases}
    by_kpi = tuple((kpi, len(cases)) for kpi, cases in worse.items() if cases)
    runs_worse = {case.split("/", 1)[0] for cases in worse.values() for case in cases}  # run-{i}
    return replace(
        result,
        tally=replace(tally, worse=len(runs_worse), worse_by_kpi=by_kpi) if stats else tally,
        gate=replace(
            findings, infra=findings.infra or bool(policy.llm_code), hidden_failures=hidden
        ),
        l2_lines=(
            *policy.calibration,
            *([""] if policy.calibration and stats else []),
            *(_l2_tables(stats, groups, gate) if stats else ()),
        ),
        timing=replace(judged, total=run.seconds + perf_counter() - start),
        calibration_lines=(*changes, *_rejudge(saved, l2[0], l2[1])),
    )


def _read_candidate(path: str, tier: str) -> CandidateRun:
    """T179: what the candidate job left. That job runs the PR's code, so the file is only that
    code's word, and one this job can't use gives no verdict."""
    try:
        run = parse_candidate(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        msg = "The candidate job left no result, so this PR has no verdict. Its log says why."
        return CandidateRun(harness_error=msg)
    except Exception as exc:  # that code wrote this file, so anything can come out of it
        return CandidateRun(
            harness_error=_harness_message("The candidate job's result can't be read", exc)
        )
    if run.head is None:
        return run
    here = _head(".") or ""
    if run.commit != here:
        msg = (
            f"The candidate job ran on `{run.commit[:7]}`, and this job reads the PR at "
            f"`{here[:7]}`: use a plain `actions/checkout` in the candidate job."
        )
        return CandidateRun(harness_error=msg)
    if run.tier != tier:
        msg = (
            f"The candidate job ran tier `{run.tier}`, and this job's is `{tier}`: give both jobs "
            "the same inputs."
        )
        return CandidateRun(harness_error=msg)
    return run


def _ci_result(args: argparse.Namespace) -> CiResult:
    start = perf_counter()
    budget = Budget(None, perf_counter)  # T089: from the start of the run; seconds below
    fail_on: FailOn = args.fail_on
    if args.harness_error:
        return CiResult(fail_on, harness_error=args.harness_error)
    policy = _gate_policy(args)
    if isinstance(policy, str):
        return CiResult(fail_on, harness_error=policy)
    if not args.candidate_in:
        return _evaluate(args, policy, _run_candidate(args, policy, budget), start)
    try:
        return _evaluate(args, policy, _read_candidate(args.candidate_in, policy.tier), start)
    except Exception as exc:  # the PR's code wrote that file, so it can break what reads it
        msg = _harness_message("The candidate job's result can't be evaluated", exc)
        return CiResult(fail_on, harness_error=msg)


def _write_candidate(args: argparse.Namespace, start: Commits) -> int:
    """T179: `--candidate-out`: only the half that runs the PR's code. Nothing it finds changes
    this exit code, since `--candidate-in` decides in another job."""
    began = perf_counter()
    budget = Budget(None, perf_counter)
    policy = args.harness_error or _gate_policy(args)
    run = (
        CandidateRun(harness_error=policy)
        if isinstance(policy, str)
        else _run_candidate(args, policy, budget)
    )
    refused = moved(start, _commits(args.base_dir))
    if refused:
        run = CandidateRun(harness_error=refused)
    commit = start.candidate or ""
    try:
        text = candidate_to_json(replace(run, commit=commit, seconds=perf_counter() - began))
    except (TypeError, ValueError) as exc:
        msg = _harness_message("this run's steps can't be saved", exc)
        text = candidate_to_json(CandidateRun(commit, harness_error=msg))
    out = Path(args.candidate_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"lgcov: what this run did is at {out}; the job with --candidate-in decides it.")
    return 0


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


def _whole_run_note(specs: list[KPISpec]) -> list[str]:
    """T181: a tool check reads every tool the run called, not only its KPI's node's (T210)."""
    tools = [s.name for s in specs if (s.check or {}).get("type") in _TOOL_CHECKS]
    return [f"⚠️ {_WHOLE_RUN}: {_quoted(tools)}."] if tools else []


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
    stopped: Collection[int] = (),
) -> tuple[list[KpiStats], GateConfig]:
    """T091: the L2 gate on recorded vs replayed outputs, as `lgcov judge` runs it. `extra`:
    model judge calls on top of the plan (T102: saved cases); `stopped`: runs replay left
    unfinished."""
    plan = _l2_plan(specs, runs, replays, stopped)
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


def _git_out(repo: str, *args: str) -> str:
    """T114: git's stdout in repo; '' when git fails there, times out, or isn't installed."""
    git = ["git", "-C", repo, *args]
    try:
        return subprocess.run(git, check=True, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _head(repo: str) -> str | None:
    """T114: the commit checked out in repo; None if it isn't a git checkout."""
    return _git_out(repo, "rev-parse", "--verify", "-q", "HEAD").strip() or None


def _commits(base_dir: str | None) -> Commits:
    """T114: HEAD of the base checkout and of the working directory, and whether a locked
    one has uncommitted changes (untracked files too), which the lock can't see."""
    base, candidate = _head(base_dir) if base_dir else None, _head(".")
    locked = [repo for repo, sha in ((base_dir, base), (".", candidate)) if repo and sha]
    return Commits(base, candidate, any(_git_out(r, "status", "--porcelain") for r in locked))


def _locked(result: CiResult, start: Commits, base_dir: str | None) -> CiResult:
    """T114: a run whose base or candidate commit moved gives no verdict: INFRA_ERROR."""
    refused = moved(start, _commits(base_dir))
    if refused:
        return CiResult(result.fail_on, harness_error=refused)
    return replace(result, locked=locked_line(start))


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
    ap.add_argument("--policy-dir", help="checkout of the PR's base: lgcov.yaml, taxonomy, bands")
    ap.add_argument("--result-out", help="write the result here, bound to commits and policy")
    ap.add_argument("--mode", choices=MODES, default="enforcing", help=_MODE_HELP)
    ap.add_argument("--approved-by", default="", help=_APPROVED_BY_HELP)
    ap.add_argument("--ci-status", choices=CI_STATUSES, default="", help=_CI_STATUS_HELP)
    ap.add_argument("--candidate-out", help="only run the PR's code; write what it did here")
    ap.add_argument("--candidate-in", help="only decide, from what --candidate-out wrote")
    args = ap.parse_args(argv)
    if args.candidate_out and args.candidate_in:
        ap.error("--candidate-out and --candidate-in are the two halves of a run; pass one")
    policy = _policy_hashes(args)  # T154: the base's, before the PR's code can touch them
    bootstrap = _first_adoption(args)
    if bootstrap:
        args.policy_dir = None  # T154: the PR's policy is all there is, so nothing is enforced
    start = _commits(args.base_dir)  # T114: before anything is loaded
    if args.candidate_out:
        return _write_candidate(args, start)
    result = replace(
        _locked(_ci_result(args), start, args.base_dir),
        first_adoption=bootstrap,
        mode=args.mode,
        approved_by=tuple(n.strip() for n in args.approved_by.split(",") if n.strip()),
        ci_status=args.ci_status,
    )
    action = result.action
    report = Path(args.report).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    body = render_ci_report(result)
    report.write_text(body, encoding="utf-8")
    if args.candidate_in:  # the runner reads `::` commands from stdout, and that file fed this
        print(f"lgcov: the report is at {report}")
    else:
        print(body, end="")
    if args.result_out:
        bound = GateResult(
            start.base or "",
            _pr_head() or start.candidate or "",
            policy,
            action.exit_code,
            action.verdict,
            file_hash(body.encode("utf-8")),
        )
        Path(args.result_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.result_out).write_text(result_to_json(bound), encoding="utf-8")
    if args.timing_out and result.timing:
        timing = Path(args.timing_out)
        timing.parent.mkdir(parents=True, exist_ok=True)
        timing.write_text(json.dumps(asdict(result.timing)) + "\n", encoding="utf-8")
    if args.github_output:
        outputs = {
            "exit-code": action.exit_code,
            "first-drift": ",".join(result.first_drift),
            "inconclusive-count": len(result.inconclusive),
            "propagated-drift-count": len(result.propagated_drift),
            "report-path": report,
            "root-drift-count": len(result.root_drift),
            "tier": result.tier,
            "verdict": action.verdict,
        }
        with open(args.github_output, "a", encoding="utf-8") as f:
            f.writelines(f"{k}={v}\n" for k, v in outputs.items())
    return action.exit_code


def _verify_result_cmd(argv: list[str]) -> int:
    """T154: check `lgcov ci`'s result before its verdict is posted. One that doesn't bind gives
    no verdict: the report becomes a harness error, and the outputs and the exit code say 2."""
    ap = argparse.ArgumentParser(prog="lgcov verify-result")
    ap.add_argument("--result", required=True, help="the file `lgcov ci --result-out` wrote")
    ap.add_argument("--report", required=True, help="the PR comment; replaced on a mismatch")
    ap.add_argument("--base-sha", required=True, help="the PR's base commit, from the event")
    ap.add_argument("--head-sha", required=True, help="the PR's head commit, from the event")
    ap.add_argument("--policy-dir", required=True, help="checkout of that base commit")
    ap.add_argument("--github-output", help="append step outputs here ($GITHUB_OUTPUT)")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="lgcov.yaml, as `lgcov ci` read it")
    ap.add_argument("--taxonomy", help="the taxonomy, as `lgcov ci` read it")
    ap.add_argument("--bands", default=_BANDS, help="the bands, as `lgcov ci` read them")
    ap.add_argument("--fail-on", type=_fail_on_arg, default="root-drift", help="for the report")
    args = ap.parse_args(argv)
    report = Path(args.report)
    result = None
    try:
        result = parse_result(Path(args.result).read_text(encoding="utf-8"))
        hashes = _policy_hashes(args)
        why = verify_result(result, args.base_sha, args.head_sha, hashes, report.read_bytes())
    except (OSError, ValueError) as exc:
        result, why = None, str(exc)
    if result is None or why:
        msg = f"The result failed verification, so this PR has no verdict: {why}"
        report.parent.mkdir(parents=True, exist_ok=True)
        body = render_ci_report(CiResult(args.fail_on, harness_error=msg))
        report.write_text(body, encoding="utf-8")
        print(msg, file=sys.stderr)
        code, shown = EXIT_ERROR, str(verdict(EXIT_ERROR))
    else:
        print(f"lgcov: result verified for base {args.base_sha} and head {args.head_sha}")
        code, shown = result.exit_code, result.verdict
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as f:
            f.write(f"exit-code={code}\nverdict={shown}\n")
    return code


_FAKE_CHECK_KEYS = ("fail_every", "when_input_contains")
_TOOL_CHECKS = ("tool_called", "tool_not_called")
_WHOLE_RUN = "node scope not applied to tool checks yet (whole-run)"
# T089: the general LLM judge client for `judge: llm` KPIs. None is built in yet: the pr tier
# never runs one, and a nightly run reports the KPIs it couldn't judge.
_llm_judge: Callable[[KPI], Judge] | None = None


def _groups(
    stats: list[KpiStats], specs: list[KPISpec], cfg: Config, gate: GateConfig
) -> list[Group]:
    """T090: the KPIs per node and slice (failure mode), as lgcov.yaml's report.group_by says."""
    if not cfg.group_by:
        return []
    where = {spec.name: (spec.node, spec.failure_mode) for spec in specs}
    return group_kpis(stats, where, cfg.group_by, gate.alpha, gate.power)


def _l2_tables(stats: list[KpiStats], groups: list[Group], gate: GateConfig) -> list[str]:
    grouped = [*render_groups(groups), ""] if groups else []
    return [
        *render_gate_table(stats, gate.alpha, gate.power),
        "",
        *grouped,
        *render_kpi_table(stats),
    ]


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


def _l2_judge(spec: KPISpec, jev_model: str | None = None, jev_timeout_s: float = 10.0) -> Judge:
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
    return JevJudge(kpi, model=jev_model, timeout_s=jev_timeout_s)


_Cases = dict[str, tuple[NodeState, NodeState]]


def _l2_cases(spec: KPISpec, runs: list[list[Step]], stopped: Collection[int] = ()) -> _Cases:
    """One case per run: the trace for code judges, else the node's own input and output. A
    run in `stopped` has no whole-run case: its trace is cut off, and its last step isn't the
    final one, so a code check would read as failed when it was never checked. The trace's
    tools are the whole run's, whatever node the KPI names (T181)."""
    cases: _Cases = {}
    for i, steps in enumerate(runs):
        if i in stopped and (spec.judge == "code" or spec.node == "final"):
            continue
        case_id = f"run-{i}/{spec.node}"
        if spec.judge == "code":
            tools = run_tools([s.output for s in steps])
            cases[case_id] = ({}, {"trace": {"nodes": [s.node for s in steps], "tools": tools}})
            continue
        if spec.node == "final":
            step = steps[-1] if steps else None
        else:
            step = next((s for s in steps if s.node == spec.node), None)
        if step is not None:
            # T165: plain, since judges send it as JSON and reviews save it as JSON
            node_in = plain(step.input) if isinstance(step.input, dict) else {}
            output = step.output if isinstance(step.output, dict) else {"output": step.output}
            node_out = plain(output)
            cases[case_id] = (node_in, node_out)
    return cases


def _l2_plan(
    specs: list[KPISpec],
    runs: list[Cassette],
    replays: list[tuple[list[Drift], list[Step]]],
    stopped: Collection[int] = (),
) -> list[tuple[KPISpec, _Cases, _Cases]]:
    base, fresh = [r.steps for r in runs], [steps for _, steps in replays]
    return [
        (spec, _l2_cases(spec, base, stopped), _l2_cases(spec, fresh, stopped)) for spec in specs
    ]


def _base_runs(args: argparse.Namespace, cfg: Config) -> tuple[list[Cassette], list[str]]:
    """The runs recorded on the base, and a note if they can't be read. None there: nothing
    to compare, as when this PR adds the first baseline."""
    path = Path(_policy_path(args, cfg.cassette_dir)) / "baseline.json"
    if not path.is_file():
        return [], []
    try:
        return _load_runs(str(path)), []
    except (OSError, ValueError, KeyError, TypeError) as exc:
        why = _harness_message("baseline.json", exc)
        return [], [f"⚠️ Could not read the base's cassettes, so they weren't compared: {why}"]


def _pair_with_base(
    runs: list[Cassette], base_runs: list[Cassette], ignore_paths: Sequence[str]
) -> tuple[list[list[Step] | None], list[str]]:
    """Each run's steps on the base, by masked input, each used once, since a re-recording can
    reorder runs; then the base runs nothing here pairs with, as `run-0: {its masked input}`."""

    def key(run: Cassette) -> str:
        return json.dumps(normalize(run.inputs, ignore_paths), sort_keys=True, default=str)

    waiting: dict[str, list[int]] = {}
    for i, run in enumerate(base_runs):
        waiting.setdefault(key(run), []).append(i)
    paired = [
        base_runs[waiting[key(run)].pop(0)].steps if waiting.get(key(run)) else None for run in runs
    ]
    left = sorted((i, key(base_runs[i])) for unpaired in waiting.values() for i in unpaired)
    cut = [(i, k[:60] + "…" if len(k) > 60 else k) for i, k in left]
    return paired, [f"run-{i}: " + k.replace("`", "'") for i, k in cut]


def _removed_note(removed: Sequence[str], cassette_dir: str) -> list[str]:
    """The base's runs this PR dropped. They block nothing, but nothing checked them either."""
    if not removed:
        return []
    what = "1 run" if len(removed) == 1 else f"{len(removed)} runs"
    verb, it = ("isn't", "it wasn't") if len(removed) == 1 else ("aren't", "they weren't")
    where = f"recorded on the base {verb} in this PR's `{cassette_dir}`"
    return [f"⚠️ {what} {where}, so {it} checked: {named_cases(removed)}."]


def _hidden_failures(
    specs: list[KPISpec],
    judges: list[Judge],
    stats: list[KpiStats],
    fresh: list[list[Step]],
    paired: list[list[Step] | None],
    stopped: Collection[int],
) -> tuple[CodeFailure, ...]:
    """Blocking code checks that pass on the base's recorded runs and fail on this PR's, though
    its own cassettes show nothing new: a re-baseline moved what they're compared with. A run
    with no partner, or one replay stopped, isn't compared. Code judges cost no model calls."""
    skipped = {*stopped, *(i for i, steps in enumerate(paired) if steps is None)}
    recorded = [steps or [] for steps in paired]
    found = []
    for spec, judge, s in zip(specs, judges, stats, strict=True):
        if spec.judge != "code" or not s.blocking:
            continue
        there, here = _l2_cases(spec, recorded, skipped), _l2_cases(spec, fresh, skipped)
        hidden = tuple(
            case
            for case in here
            if case not in s.new_failures
            and judge_samples(judge, *there[case], 1)[0].passed is True
            and judge_samples(judge, *here[case], 1)[0].passed is False
        )
        if hidden:
            found.append(CodeFailure(s.kpi, hidden, len(here)))
    return tuple(found)


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
    return gate_kpis(stats, advisory, gate.alpha, flagged=flagged, power=gate.power)


def _saved_pair(folder: Path) -> tuple[list[Cassette], list[tuple[list[Drift], list[Step]]]]:
    """T195: the base runs `lgcov bench --keep-runs` saved in <folder>, and head's as their
    replays, compared as `lgcov bench --from-runs` compares them."""
    runs, fresh = (_load_runs(str(folder / name)) for name in _KEPT)
    if len(runs) != len(fresh):
        raise ValueError(f"{len(runs)} runs on base, {len(fresh)} on head")
    return runs, [
        (list(compared_run(r, f.steps, (), None).drifts), list(f.steps))
        for r, f in zip(runs, fresh, strict=True)
    ]


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
    ap.add_argument(
        "--from-runs",
        metavar="DIR",
        help="judge the base.json and head.json `lgcov bench --keep-runs` saved in DIR; "
        "nothing is replayed, so the agent makes no call",
    )
    args = ap.parse_args(argv)
    if args.from_runs and args.live:
        print("lgcov judge: --from-runs runs nothing: no --live", file=sys.stderr)
        return EXIT_ERROR
    start = _commits(None)  # T114: the candidate; its base is the cassette in that checkout
    # T195: saved runs need no graph, so a stand-in entrypoint validates the config
    entrypoint = args.graph or ("from-runs.py:unused" if args.from_runs else None)
    overrides = {"graph_entrypoint": entrypoint, "cassette_dir": args.cassette_dir}
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
        judges = [_l2_judge(spec, cfg.jev_model, cfg.jev_timeout_s) for spec in specs]
    except (OSError, ValueError) as exc:
        print(f"lgcov judge: invalid taxonomy: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if skipped:
        print("Skipped, no llm client: " + ", ".join(f"`{n}`" for n in skipped) + ".")
    for line in _whole_run_note(specs):
        print(line)
    try:
        advisory, flagged, calibration = _gate_bands(args.bands, every, replace(cfg, gate=gate))
    except (OSError, ValueError) as exc:
        print(f"lgcov judge: invalid config or bands.json: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if args.from_runs:
        try:
            runs, replays = _saved_pair(Path(args.from_runs))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return _harness_error("lgcov judge", "cannot read saved runs", exc)
    else:
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
            done = [_replay_under(cfg, graph, r, serve=not args.live) for r in runs]
            replays = [(list(r.drifts), list(r.steps)) for r in done]
            _say_replay_notes(done)
        except NodeError as exc:
            return _node_failure("lgcov judge", exc)
        except Exception as exc:  # replay itself: no node or router raised it
            return _harness_error("lgcov judge", "replay failed outside node code", exc)
    plan = _l2_plan(specs, runs, replays)
    saved = _saved_cases(Path(args.bands).parent / "cases", specs)
    total, model = _l2_calls(plan, k)
    total += sum(len(c) for c in saved.values())  # T102: each saved case, judged once more
    model += sum(len(saved.get(s.name, [])) for s in specs if s.judge != "code")
    print(locked_line(start))
    print(f"Planned: {total} judge calls, {model} to model judges (--max-calls {args.max_calls})")
    if model > args.max_calls:
        print(
            f"lgcov judge: refusing to make {model} model judge calls (more than "
            f"--max-calls {args.max_calls}); lower --samples or raise --max-calls",
            file=sys.stderr,
        )
        return EXIT_ERROR
    stats = _l2_gate(plan, judges, k, gate, runs, replays, advisory, flagged)
    rechecked = _rejudge(saved, specs, judges)
    refused = moved(start, _commits(None))  # T114: no verdict from two versions of the code
    if refused:
        print(f"lgcov judge: {refused}", file=sys.stderr)
        return EXIT_ERROR
    groups = _groups(stats, specs, cfg, gate)
    print("\n".join([*calibration, *([""] if calibration else [])]), end="")
    print("\n".join(_l2_tables(stats, groups, gate)))
    if rechecked:
        print("\n".join(["", *rechecked]))
    code = groups_exit(
        gate_exit_code(stats, gate.inconclusive_blocks), groups, gate.inconclusive_blocks
    )
    return code


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


def _metrics_cmd(argv: list[str]) -> int:
    """T156: summarize the per-PR records in a JSONL file."""
    if argv[:1] != ["summary"]:
        print("usage: lgcov metrics summary FILE", file=sys.stderr)
        return EXIT_ERROR
    ap = argparse.ArgumentParser(prog="lgcov metrics summary")
    ap.add_argument("file", help="the per-PR records, one JSON object per line")
    args = ap.parse_args(argv[1:])
    try:
        records = parse_records(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"lgcov metrics summary: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(format_summary(summarize(records)))
    return 0


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
        done = [_replay_under(cfg, graph, run, serve=True) for run in runs]
    except Exception as exc:
        return _harness_error("lgcov review", "cannot replay the graph", exc)
    _say_replay_notes(done)
    fresh = [list(r.steps) for r in done]
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


def _bench_sides(b: Benchmark, tmp: Path) -> tuple[str, str, list[Path]]:
    """T101a: the base and head graphs as targets, from whole-file patches or git refs, and
    the git worktrees made for them (to remove afterwards)."""
    file, _, attr = b.graph.rpartition(":")
    if b.base_patch and b.head_patch:
        sides = []
        for side, patch in (("base", b.base_patch), ("head", b.head_patch)):
            dest = tmp / side / file
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text((b.folder / patch).read_text(encoding="utf-8"), encoding="utf-8")
            sides.append(f"{dest}:{attr}")
        return sides[0], sides[1], []
    repo = (b.folder / b.repo).resolve()
    worktrees: list[Path] = []
    for side, ref in (("base", b.base_ref), ("head", b.head_ref)):
        git = ["git", "-C", str(repo), "worktree", "add", "--detach", str(tmp / side), str(ref)]
        subprocess.run(git, check=True, capture_output=True, text=True)
        worktrees.append(tmp / side)
    return f"{tmp / 'base' / file}:{attr}", f"{tmp / 'head' / file}:{attr}", worktrees


_KEPT = ("base.json", "head.json")  # T169: what --keep-runs saves, per benchmark


class _SideFailed(Exception):
    """T166: one side of a benchmark failed in its own process."""


class _CantImport(_SideFailed):
    """T166: one side of a benchmark couldn't load its graph, so nothing was run for it."""


def _run_side(b: Benchmark, side: str, root: Path, *args: str) -> None:
    """T166: one step of one side, in its own process (lgcov.bench_side). The side's root and
    `pythonpath` come first on its path, ahead of anything installed, and it inherits the
    environment, where an agent's API keys live. What it prints follows on stderr once the step
    is done, named by side: stdout is the report."""
    path = [str(root), *(str(root / p) for p in b.pythonpath)]
    if inherited := os.environ.get("PYTHONPATH"):
        path.append(inherited)
    done = subprocess.run(
        [sys.executable, "-m", "lgcov.bench_side", *args],
        cwd=root,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(path)},
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    for line in done.stdout.splitlines():
        print(f"[{side}] {line}", file=sys.stderr)
    lines = done.stderr.splitlines()
    if done.returncode == 0:
        sys.stderr.write(done.stderr)  # what it had to say, like rebind_skipped
        return
    why = lines.pop() if lines else f"exit code {done.returncode}"
    print("\n".join(lines), file=sys.stderr, end="\n" if lines else "")
    if done.returncode == CANT_IMPORT:
        raise _CantImport(f"cannot import the {side} side: {why}")
    raise _SideFailed(f"{side} side: {why}")


def _error_row(b: Benchmark, error: str, seconds: float) -> Result:
    return Result(
        b.name,
        (),
        (),
        Score(0, 0, 0),
        Score(0, 0, 0),
        seconds,
        tuple(sorted(b.root)),
        tuple(sorted(b.propagated)),
        b.hard_cases,
        b.notes,
        error=error,
    )


def _bench_one(b: Benchmark, keep: Path | None = None, live: bool = False) -> Result:
    """Record the cases on base, replay head (unchanged nodes served, as `lgcov ci` does),
    and score lgcov's root / propagated labels. A benchmark that can't run says why.

    T166: each side runs in its own process. Head's import is checked first, and base's is the
    first thing its recording does, so a bad import costs no real call on either side.

    T169: with `keep`, each side's runs stay in <keep>/<benchmark>/, saved as each one finishes.

    T173: with `live`, head serves nothing, so base against base measures the model's noise."""
    start = perf_counter()
    worktrees: list[Path] = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                base_target, head_target, worktrees = _bench_sides(b, Path(tmp))
                base_root, head_root = Path(tmp) / "base", Path(tmp) / "head"
                cases, drifted = Path(tmp) / "cases", Path(tmp) / "drifted"
                probed = Path(tmp) / "probes"
                here = Path(tmp) if keep is None else keep / b.name
                saved, fresh = here / "base.json", here / "head.json"
                cases.write_text(json.dumps(list(b.cases)), encoding="utf-8")
                _run_side(b, "head", head_root, "check", head_target)
                _run_side(b, "base", base_root, "record", base_target, str(cases), str(saved))
                replaying = [head_target, str(saved), str(drifted), str(fresh), str(probed)]
                replaying += ["--live"] if live else []
                _run_side(b, "head", head_root, "replay", *replaying)
                runs = _load_runs(str(saved))
                heads = _load_runs(str(fresh))
                nodes: list[list[str]] = json.loads(drifted.read_text(encoding="utf-8"))
                drifts = [[Drift(n, None, None) for n in names] for names in nodes]
                raw = json.loads(probed.read_text(encoding="utf-8"))
                probes = {n: Probe(drifted, why) for n, (drifted, why) in raw.items()}
            finally:
                for worktree in worktrees:
                    remove = ["git", "-C", str(worktree), "worktree", "remove", "--force", "."]
                    subprocess.run(remove, check=False, capture_output=True)
    except _CantImport:
        raise  # the command's to report: it changes the exit code
    except _SideFailed as exc:  # a node crashed, say: a row, not a crash
        return _error_row(b, str(exc)[:200], perf_counter() - start)
    except Exception as exc:  # git can raise anything too
        failed = getattr(exc, "stderr", "") or str(exc)
        error = f"{type(exc).__name__}: {str(failed).strip()[:200]}"
        return _error_row(b, error, perf_counter() - start)
    return _labelled(b, (runs, heads), drifts, probes, perf_counter() - start)


def _bench_saved(b: Benchmark, folder: Path) -> Result:
    """T162: score the runs `--keep-runs` saved in <folder>/<benchmark>/, compared as replay
    compares them. Nothing runs, so it costs nothing, whatever the row's `status`."""
    start = perf_counter()
    try:
        runs, fresh = (_load_runs(str(folder / b.name / name)) for name in _KEPT)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _error_row(b, f"{type(exc).__name__}: {str(exc)[:200]}", perf_counter() - start)
    if len(runs) != len(fresh):
        return _error_row(b, f"{len(runs)} runs on base, {len(fresh)} on head", 0.0)
    drifts = [
        list(compared_run(r, f.steps, (), None).drifts) for r, f in zip(runs, fresh, strict=True)
    ]
    return _labelled(b, (runs, fresh), drifts, {}, perf_counter() - start)


def _labelled(
    b: Benchmark,
    sides: tuple[list[Cassette], list[Cassette]],
    drifts: list[list[Drift]],
    probes: Mapping[str, Probe],
    seconds: float,
) -> Result:
    """T162: root by fingerprint and probe (`classify`); the first node to drift is kept too."""
    counts, first = _drift_kinds(sides[0], drifts)
    base, head = ({s.node: s.code for r in side for s in r.steps} for side in sides)
    firsts = [_run_root(r, d) for r, d in zip(sides[0], drifts, strict=True)]
    nodes = [{x.node for x in d} for d in drifts]
    # T175: a changed router makes its source node root, as in `lgcov ci`.
    base_routers = {s.node: s.router for r in sides[0] for s in r.steps}
    routers = (base_routers, {s.node: s.router for r in sides[1] for s in r.steps})
    ran = [[s.node for s in r.steps] for r in sides[0]]
    by_node = classify(counts.keys(), (base, head), probes)
    labels = explain_runs(by_node, firsts, nodes, routers, ran)
    return Result(
        b.name,
        labels.root,
        labels.propagated,
        score(labels.root, b.root),
        score(labels.propagated, b.propagated),
        seconds,
        tuple(sorted(b.root)),
        tuple(sorted(b.propagated)),
        b.hard_cases,
        b.notes,
        first_drift=tuple(sorted(first)),
        inconclusive=tuple(sorted(labels.inconclusive.items())),
    )


def _synthetic_agents() -> Sequence[Agent]:
    """T121: the suite's agents live in examples/, which isn't in the wheel."""
    sys.path.insert(0, str(Path.cwd()))
    agents: Sequence[Agent] = importlib.import_module("examples.synthetic_agents").AGENTS
    return agents


def _lgcov_root(case: Case) -> set[str]:
    """What `lgcov ci` would call root: replay the edited agent, serving what it left alone. The
    first node to drift, or T175: the source node of a router the edit changed."""
    done = replay_run(case.graph, case.base, (), serve=True)
    root = _run_root(case.base, list(done.drifts))
    routers = (
        {s.node: s.router for s in case.base.steps},
        {s.node: s.router for s in done.steps},
    )
    ran, drifted = [s.node for s in case.base.steps], {d.node for d in done.drifts}
    routing = explain_runs(NodeLabels((), (), {}), [root], [drifted], routers, [ran]).routing
    return set(routing) or ({root} if root else set())


def _bench_synthetic(out: Path, results: Path) -> int:
    """T121: offline, so it costs nothing. A report, not a gate: it exits 0 whatever the scores."""
    try:
        agents = _synthetic_agents()
    except ModuleNotFoundError as exc:
        print(f"lgcov bench: --synthetic needs a repo checkout's examples/: {exc}", file=sys.stderr)
        return EXIT_ERROR
    cases = [case for agent in agents for case in every_case(agent)]
    aa = [(agent.name, arm) for agent in agents for arm in aa_arms(agent)]
    noise = [(agent.name, rate) for agent in agents for rate in noise_rule_rates(agent)]
    report = build_report(cases, _lgcov_root, aa, datetime.now(UTC).date().isoformat(), noise)
    section = render_section(report)
    doc = out.read_text(encoding="utf-8") if out.exists() else ""
    for path, text in ((out, replace_section(doc, section)), (results, report_json(report))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(section, end="")
    return 0


def _bench_cmd(argv: list[str]) -> int:
    """T101a: lgcov's root / propagated labels against hand-written ones, one benchmark file
    each. A report, not a gate: it exits 0 whatever the scores. T166: it exits 2 if a side's
    graph can't be imported, after the other benchmarks have run and the report is written."""
    ap = argparse.ArgumentParser(prog="lgcov bench")
    ap.add_argument("dir", nargs="?", help="benchmark files (*.yaml); default docs/benchmark")
    ap.add_argument("--only", action="append", default=[], help="run this one (file name)")
    ap.add_argument("--out", default="docs/benchmark.md", help="write the table here")
    ap.add_argument("--json", help="and the numbers; default docs/benchmark-results.json")
    ap.add_argument(
        "--synthetic",
        action="store_true",
        help="T121: score the synthetic suite instead, and replace only that section of --out",
    )
    ap.add_argument(
        "--keep-runs",
        metavar="DIR",
        help="save each side's runs in DIR/<benchmark>/{base,head}.json as each one finishes",
    )
    ap.add_argument("--live", action="store_true", help="T173: head runs every node, serving none")
    ap.add_argument(
        "--from-runs",
        metavar="DIR",
        help="T162: score the runs --keep-runs saved in DIR instead of running the agent",
    )
    args = ap.parse_args(argv)
    if args.from_runs and (args.synthetic or args.keep_runs or args.live):
        print(
            "lgcov bench: --from-runs runs nothing: no --synthetic, --keep-runs or --live",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if args.synthetic:
        if args.dir or args.only or args.keep_runs or args.live:
            print("lgcov bench: --synthetic takes only --out and --json", file=sys.stderr)
            return EXIT_ERROR
        return _bench_synthetic(Path(args.out), Path(args.json or "docs/benchmark-synthetic.json"))
    args.json = args.json or "docs/benchmark-results.json"
    folder = Path(args.dir or "docs/benchmark")
    files = [f for f in sorted(folder.glob("*.yaml")) if not args.only or f.stem in args.only]
    if not files:
        print(f"lgcov bench: no benchmark files (*.yaml) to run in {folder}", file=sys.stderr)
        return EXIT_ERROR
    benchmarks = []
    for f in files:
        try:
            raw: object = yaml.safe_load(f.read_text(encoding="utf-8"))
            benchmarks.append(parse_benchmark(f.stem, raw, f.parent))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            print(f"lgcov bench: {f}: {exc}", file=sys.stderr)
            return EXIT_ERROR
    # T169: resolved here, because each side runs from its own folder.
    keep = Path(args.keep_runs).resolve() if args.keep_runs else None
    if keep is not None:
        names = [keep / b.name / f for b in benchmarks if not b.status for f in _KEPT]
        for taken in filter(Path.exists, names):
            print(
                f"lgcov bench: {taken} already holds a recorded run: "
                "choose another --keep-runs folder",
                file=sys.stderr,
            )
            return EXIT_ERROR
    results: list[Result] = []
    code = 0
    for b in benchmarks:
        try:
            if args.from_runs:
                results.append(_bench_saved(b, Path(args.from_runs)))
            else:
                results.append(pending_result(b) if b.status else _bench_one(b, keep, args.live))
        except _CantImport as exc:
            print(f"lgcov bench: {b.name}: {exc}", file=sys.stderr)
            results.append(_error_row(b, str(exc)[:200], 0.0))
            code = EXIT_ERROR
    saved = [f"--from-runs {args.from_runs}"] if args.from_runs else []
    command = " ".join(["lgcov bench", str(folder), *(f"--only {n}" for n in args.only), *saved])
    source = f"the {len(results)} benchmark file{'' if len(results) == 1 else 's'} in `{folder}`"
    md = render_markdown(results, command, datetime.now(UTC).date().isoformat(), source)
    for path, text in ((Path(args.out), md), (Path(args.json), results_json(results))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(md, end="")
    if keep is not None:
        print(f"lgcov bench: runs kept in {keep}", file=sys.stderr)
    return code


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


def _lint_cmd(argv: list[str]) -> int:
    """T104: `lgcov lint`: what is wrong with the graph's shape, one line per finding, then
    "🧹 Lint: N warnings". Exit 0; --strict exits 1 on a warning (info never counts)."""
    ap = argparse.ArgumentParser(prog="lgcov lint")
    ap.add_argument(
        "target",
        nargs="?",
        help="<file.py>:<attr> (overrides graph_entrypoint), or a folder with graph.py and "
        "maybe cassettes/ and taxonomy.yaml",
    )
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="lgcov.yaml (optional)")
    ap.add_argument("--cassette-dir", help="overrides cassette_dir")
    ap.add_argument("--taxonomy", help="taxonomy.yaml; overrides taxonomy_path")
    ap.add_argument("--strict", action="store_true", help="exit 1 on any warning")
    args = ap.parse_args(argv)
    folder = Path(args.target) if args.target and Path(args.target).is_dir() else None
    if folder is not None:  # a project folder, like examples/demo_agent: no lgcov.yaml
        tax = folder / "taxonomy.yaml"
        entry = f"{folder / 'graph.py'}:graph"
        cassettes = args.cassette_dir or str(folder / "cassettes")
        taxonomy = args.taxonomy or (str(tax) if tax.is_file() else None)
    else:
        overrides = {
            "graph_entrypoint": args.target,
            "cassette_dir": args.cassette_dir,
            "taxonomy_path": args.taxonomy,
        }
        try:
            cfg = load_config(args.config, overrides)
        except (OSError, ValueError) as exc:
            print(f"lgcov lint: invalid config: {exc}", file=sys.stderr)
            return EXIT_ERROR
        entry, cassettes, taxonomy = cfg.graph_entrypoint, cfg.cassette_dir, cfg.taxonomy_path
    try:
        # Exception: importing user graph code can raise anything.
        graph = _load_graph(entry)
        kpi_nodes = {s.node for s in load_taxonomy(taxonomy).kpi_specs} if taxonomy else None
    except Exception as exc:
        return _harness_error("lgcov lint", "cannot load the graph or the taxonomy", exc)
    baseline, hit = Path(cassettes) / "baseline.json", None
    if baseline.is_file():
        try:
            hit = {s.node for run in _load_runs(str(baseline)) for s in run.steps}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return _harness_error("lgcov lint", "cannot read baseline", exc)
    else:
        print(f"No {baseline}: never-hit check skipped.")
    found = lint(snapshot_from_compiled(graph), routers_of(graph), hit, kpi_nodes)
    print("\n".join([*(f.line() for f in found), summary(found)]))
    return 1 if args.strict and any(f.severity == "warn" for f in found) else 0


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
                policy_dir=None,
                candidate_in=None,
            )
        )
        actual = Outcome(result.action.exit_code)
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


def _round_ledger(path: Path) -> str:
    """The exposure ledger next to a round file; '' before its first row."""
    ledger = path.with_name("exposure.jsonl")
    return ledger.read_text(encoding="utf-8") if ledger.exists() else ""


def _append_ledger(path: Path, lines: str) -> None:
    with path.with_name("exposure.jsonl").open("a", encoding="utf-8") as ledger:
        ledger.write(lines)


def _round_lock(path: Path, now: str) -> int:
    """T119: refuses tracked changes anywhere and untracked files in the round's folder; an
    untracked file elsewhere only gets a warning, since it can't change what the round locks."""
    round_ = parse_round(path.read_text(encoding="utf-8"))
    rows = parse_ledger(_round_ledger(path))
    folder = str(path.resolve().parent)
    if _head(folder) is None:
        raise ValueError(f"{folder} isn't a git checkout with a commit, so nothing can be locked")
    status = _git_out(folder, "status", "--porcelain", "-z", "--untracked-files=all")
    tree = tree_state(status, _git_out(folder, "rev-parse", "--show-prefix").strip())
    if tree.tracked_changes:
        raise ValueError(f"uncommitted changes: {', '.join(tree.tracked_changes)}")
    if tree.untracked_in_folder:
        raise ValueError(f"untracked in the round's folder: {', '.join(tree.untracked_in_folder)}")
    if tree.untracked_elsewhere:
        print(
            f"warning: untracked, outside the round: {', '.join(tree.untracked_elsewhere)}",
            file=sys.stderr,
        )
    locked, lock_rows = lock_round(round_, rows, now)
    _append_ledger(path, ledger_lines(lock_rows))
    path.write_text(round_to_yaml(locked), encoding="utf-8")
    print(f"Locked round {locked.id}. Commit {path.name} and exposure.jsonl.")
    return 0


def _round_check(path: Path, run_id: str | None, now: str) -> int:
    round_ = parse_round(path.read_text(encoding="utf-8"))
    problems = check_round(round_, parse_ledger(_round_ledger(path)))
    for problem in problems:
        print(f"lgcov round check: {problem}", file=sys.stderr)
    if problems:
        return EXIT_ERROR
    if run_id:
        _append_ledger(path, ledger_lines(exposures(round_, run_id, now)))
    print(f"Round {round_.id} is clean.")
    return 0


def _round_cmd(argv: list[str]) -> int:
    """T119: a benchmark round: init writes a template, lock freezes it, check verifies it."""
    ap = argparse.ArgumentParser(prog="lgcov round")
    verbs = ap.add_subparsers(dest="verb", required=True)
    init = verbs.add_parser("init", help="write an unfrozen round to fill in")
    init.add_argument("--id", required=True, help="the round's id, unique in its ledger")
    lock = verbs.add_parser("lock", help="freeze the round and record its SHAs in the ledger")
    check = verbs.add_parser("check", help="exit 2 on a moved SHA or a re-exposed case")
    check.add_argument("--record", metavar="RUN_ID", help="after a clean check, record this run")
    for verb in (init, lock, check):
        verb.add_argument("path", help="the round file; exposure.jsonl sits next to it")
    args = ap.parse_args(argv)
    path, now = Path(args.path), datetime.now(UTC).isoformat(timespec="seconds")
    try:
        if args.verb == "lock":
            return _round_lock(path, now)
        if args.verb == "check":
            return _round_check(path, args.record, now)
        if path.exists():
            raise ValueError(f"{path} already exists")
        path.write_text(round_to_yaml(new_round(args.id, now)), encoding="utf-8")
        print(f"Wrote {path}. Fill in its candidates and cases, commit, then lock it.")
        return 0
    except (OSError, ValueError) as exc:
        print(f"lgcov round {args.verb}: {exc}", file=sys.stderr)
        return EXIT_ERROR


def _command(argv: list[str]) -> int | None:
    """Run the lgcov command argv names and return its exit code; None if it names none."""
    if argv[:1] == ["--version"]:
        print(f"lgcov {version('lgcov')}")
        return 0
    if argv[:2] == ["taxonomy", "draft"]:
        return _taxonomy_draft(argv[2:])
    if argv[:2] == ["taxonomy", "approve"]:
        return _taxonomy_approve(argv[2:])
    if argv[:1] == ["taxonomy"]:
        return _taxonomy_check(argv[1:])
    if argv[:1] == ["replay"]:
        return _replay(argv[1:])
    if argv[:1] == ["snapshot"]:
        return _snapshot(argv[1:])
    if argv[:1] == ["selfcheck"]:
        return _selfcheck_cmd(argv[1:])
    if argv[:1] == ["lint"]:
        return _lint_cmd(argv[1:])
    if argv[:1] == ["judge"]:
        return _judge_cmd(argv[1:])
    if argv[:1] == ["calibrate"]:
        return _calibrate_cmd(argv[1:])
    if argv[:1] == ["review"]:
        return _review_cmd(argv[1:])
    if argv[:1] == ["round"]:
        return _round_cmd(argv[1:])
    if argv[:1] == ["bench"]:
        return _bench_cmd(argv[1:])
    if argv[:1] == ["metrics"]:
        return _metrics_cmd(argv[1:])
    if argv[:1] == ["coverage"]:
        return _coverage_cmd(argv[1:])
    if argv[:1] == ["ci"]:
        return _ci(argv[1:])
    if argv[:1] == ["verify-result"]:
        return _verify_result_cmd(argv[1:])
    if argv[:1] == ["record"]:
        return _record(argv[1:])
    if argv[:2] == ["import", "langsmith"]:
        return _import_langsmith(argv[2:])
    if argv[:1] == ["import"]:
        print(
            "lgcov import: the one source is langsmith (lgcov import langsmith --dataset X)",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if argv[:1] == ["cassette"]:
        return _cassette_accept(argv[1:])
    return None


def _graph_diff(argv: list[str]) -> int:
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


def main(argv: list[str] | None = None) -> int:
    """The `graph-diff` script: an lgcov command, else the diff of two graph snapshots."""
    argv = sys.argv[1:] if argv is None else argv
    code = _command(argv)
    return _graph_diff(argv) if code is None else code


_LGCOV_HELP = """\
usage: lgcov [-h] [--version] COMMAND ...

Catch silent regressions in LangGraph agents on every PR.

commands:
  record            record a baseline: one cassette run per input in --inputs
  replay            replay the baseline and report root and propagated drift
  cassette accept   re-baseline drifted nodes (--nodes A,B or --all-drifted)
  ci                what the GitHub Action runs: replay, gates and the PR report
  verify-result     check a result from `lgcov ci` before its verdict is posted
  snapshot          print the graph's nodes and edges as a JSON snapshot
  coverage          coverage of the recorded runs, and its trend against main
  lint              check the graph's shape before anything runs
  judge             run the taxonomy's judges k times per case
  calibrate         compare each judge with human labels and set its band
  review override   override a judge's verdict on one case
  taxonomy check    validate a taxonomy and print its KPIs
  taxonomy draft    draft failure modes and KPIs from traces, each pending review
  taxonomy approve  approve one drafted entry
  import langsmith  turn a LangSmith dataset's examples into lgcov cases
  round init        write an unfrozen benchmark round to fill in
  round lock        freeze a benchmark round and record its SHAs
  round check       exit 2 if a locked SHA moved or a case was already exposed
  bench             score the root / propagated labels against hand-written ones
  metrics summary   summarize per-PR records: overridden blocks, label burden, cost, time
  selfcheck         run the harness on fixtures whose outcome is known

options:
  -h, --help        show this help and exit
  --version         print lgcov's version and exit

lgcov COMMAND --help shows a command's options.
graph-diff OLD NEW prints the node and edge changes between two graph snapshots."""


def lgcov_main(argv: list[str] | None = None) -> int:
    """The `lgcov` script: lgcov's commands, its own help for -h, --help and no command,
    and an error for anything else. `graph-diff` runs `main`, which diffs two snapshots."""
    argv = sys.argv[1:] if argv is None else argv
    asked = argv[:1] in (["-h"], ["--help"])
    if not argv or asked:
        print(_LGCOV_HELP, file=sys.stdout if asked else sys.stderr)
        return 0 if asked else EXIT_ERROR
    code = _command(argv)
    if code is None:
        print(
            f"lgcov: unknown command {argv[0]!r} (lgcov --help lists the commands)",
            file=sys.stderr,
        )
        return EXIT_ERROR
    return code


if __name__ == "__main__":
    raise SystemExit(main())
