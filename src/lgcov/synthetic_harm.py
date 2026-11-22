"""T262: harm status for synthetic cases from committed code-check taxonomies.

Uses lgcov's own code judges on base vs PR cassettes only. Does not call or copy the
suite's end-to-end outcome helper, and does not read a suite label when deciding harm
status. Scoring helpers may take precomputed statuses beside labels supplied by the caller.

T264: content checks (`output_*`) read the named node's output (via `with_units`);
`when:` skips leave the KPI unjudged (unmeasured, never a silent pass). `judge: fake`
never measures a root.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from lgcov.cassette import Cassette, Step, with_units
from lgcov.code_judges import CONTENT_CHECK_TYPES, check_in_scope, code_judge
from lgcov.drift_gate import HarmStatus, root_harm_statuses
from lgcov.messages import plain, run_tools
from lgcov.taxonomy import KPISpec, load_taxonomy

# Agent name -> taxonomy path relative to the repo root. Written and committed before measure.
TAXONOMY_BY_AGENT: dict[str, str] = {
    "demo": "examples/synthetic_agents/demo_taxonomy.yaml",
    "fanout": "examples/synthetic_agents/fanout_taxonomy.yaml",
    "subgraph": "examples/synthetic_agents/subgraph_taxonomy.yaml",
}


def _trace_and_content(
    steps: Sequence[Step],
    *,
    node: str,
    kind: str,
) -> dict[str, object] | None:
    """Judge input like CI `_l2_cases`: trace for trajectory; node output for content."""
    units = with_units(steps)
    tools = run_tools([s.output for s in steps])
    out: dict[str, object] = {"trace": {"nodes": [s.node for s in units], "tools": tools}}
    if kind not in CONTENT_CHECK_TYPES:
        return out
    step = next((s for s in units if s.node == node), None)
    if step is None:
        return None
    output = step.output if isinstance(step.output, dict) else {"output": step.output}
    out.update(plain(output) if isinstance(output, dict) else {"output": output})
    return out


def code_check_new_failures(
    base: Cassette,
    pr: Cassette,
    specs: Sequence[KPISpec],
) -> tuple[frozenset[str], frozenset[str]]:
    """KPIs that newly fail on `pr` (pass on `base`), and every code KPI that was judged.

    Code judges only. A check that fails on both sides is not new. `when:` out of scope
    (base inputs) is not judged. A content check whose named node is missing is not judged.
    """
    failed: set[str] = set()
    judged: set[str] = set()
    for spec in specs:
        if spec.judge != "code" or not spec.check:
            continue
        check = spec.check
        if not check_in_scope(check, base.inputs):
            continue
        kind = str(check.get("type", ""))
        base_out = _trace_and_content(base.steps, node=spec.node, kind=kind)
        pr_out = _trace_and_content(pr.steps, node=spec.node, kind=kind)
        if base_out is None or pr_out is None:
            continue
        judge = code_judge(spec.to_kpi(), check)
        base_v = judge.judge(dict(base.inputs), base_out)
        pr_v = judge.judge(dict(pr.inputs), pr_out)
        judged.add(spec.name)
        if base_v.label == "pass" and pr_v.label == "fail":
            failed.add(spec.name)
    return frozenset(failed), frozenset(judged)


def _run_path(cassette: Cassette) -> tuple[str, ...]:
    """First-seen node order from a recorded trajectory (for upstream detection)."""
    seen: list[str] = []
    for step in with_units(cassette.steps):
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
    # Include fake KPIs in covering so judge:fake is explicitly non-observing (never measures).
    checks: dict[str, dict[str, object] | None] = {
        s.name: (dict(s.check) if s.check is not None else None) for s in specs
    }
    kpi_judges = {s.name: s.judge for s in specs}
    path = _run_path(pr) or _run_path(base)
    return root_harm_statuses(
        roots,
        {s.name: s.node for s in specs},
        failed,
        judged,
        kpi_checks=checks,
        path=path,
        kpi_judges=kpi_judges,
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
