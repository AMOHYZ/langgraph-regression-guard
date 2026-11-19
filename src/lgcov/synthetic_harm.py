"""T262: harm status for synthetic cases from committed code-check taxonomies.

Uses lgcov's own code judges on base vs PR cassettes only. Does not call or copy the
suite's end-to-end outcome helper, and does not read a suite label when deciding harm
status. Scoring helpers may take precomputed statuses beside labels supplied by the caller.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from lgcov.cassette import Cassette, Step
from lgcov.code_judges import code_judge
from lgcov.drift_gate import HarmStatus, root_harm_statuses
from lgcov.messages import run_tools
from lgcov.taxonomy import KPISpec, load_taxonomy

# Agent name -> taxonomy path relative to the repo root. Written and committed before measure.
TAXONOMY_BY_AGENT: dict[str, str] = {
    "demo": "examples/synthetic_agents/demo_taxonomy.yaml",
    "fanout": "examples/synthetic_agents/fanout_taxonomy.yaml",
    "subgraph": "examples/synthetic_agents/subgraph_taxonomy.yaml",
}


def _trace_output(steps: Sequence[Step]) -> dict[str, object]:
    tools = run_tools([s.output for s in steps])
    return {"trace": {"nodes": [s.node for s in steps], "tools": tools}}


def code_check_new_failures(
    base: Cassette,
    pr: Cassette,
    specs: Sequence[KPISpec],
) -> tuple[frozenset[str], frozenset[str]]:
    """KPIs that newly fail on `pr` (pass on `base`), and every code KPI that was judged.

    Code judges only. A check that fails on both sides is not new.
    """
    failed: set[str] = set()
    judged: set[str] = set()
    for spec in specs:
        if spec.judge != "code" or not spec.check:
            continue
        judge = code_judge(spec.to_kpi(), spec.check)
        base_v = judge.judge({}, _trace_output(base.steps))
        pr_v = judge.judge({}, _trace_output(pr.steps))
        judged.add(spec.name)
        if base_v.label == "pass" and pr_v.label == "fail":
            failed.add(spec.name)
    return frozenset(failed), frozenset(judged)


def _run_path(cassette: Cassette) -> tuple[str, ...]:
    """First-seen node order from a recorded trajectory (for upstream detection)."""
    seen: list[str] = []
    for step in cassette.steps:
        if step.node not in seen:
            seen.append(step.node)
    return tuple(seen)


def harm_for_roots(
    base: Cassette,
    pr: Cassette,
    roots: Sequence[str],
    taxonomy: Path,
) -> dict[str, HarmStatus]:
    """Harm status per root from the taxonomy's code checks on these two cassettes."""
    specs = load_taxonomy(taxonomy).kpi_specs
    failed, judged = code_check_new_failures(base, pr, specs)
    code = [s for s in specs if s.judge == "code"]
    checks: dict[str, dict[str, str] | None] = {
        s.name: (dict(s.check) if s.check is not None else None) for s in code
    }
    # Prefer the PR path; fall back to base when the PR never visited the root.
    path = _run_path(pr) or _run_path(base)
    return root_harm_statuses(
        roots,
        {s.name: s.node for s in code},
        failed,
        judged,
        kpi_checks=checks,
        path=path,
    )


def harm_rate_counts(
    rows: Sequence[tuple[str, Mapping[str, HarmStatus]]],
) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    """Score precomputed statuses. Each row is (label, statuses) from the caller.

    Returns ((enforced_ff_hits, of), (measured_recall_hits, of), (unmeasured_rooted_hits, of)).
    `label` picks benign vs failing denominators only; statuses must already have been
    computed without reading suite labels.
    """
    benign = [(lab, st) for lab, st in rows if lab == "benign"]
    failing = [(lab, st) for lab, st in rows if lab == "regressed"]
    rooted = [(lab, st) for lab, st in rows if st]

    def any_harmed(st: Mapping[str, HarmStatus]) -> bool:
        return any(s == "harmed" for s in st.values())

    def measured(st: Mapping[str, HarmStatus]) -> bool:
        return any(s != "unmeasured" for s in st.values())

    measured_failing = [st for _, st in failing if measured(st)]
    return (
        (sum(1 for _, st in benign if any_harmed(st)), len(benign)),
        (sum(1 for st in measured_failing if any_harmed(st)), len(measured_failing)),
        (sum(1 for _, st in rooted if st and not measured(st)), len(rooted)),
    )
