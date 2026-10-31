"""T090: KPI results per node and per slice (failure mode); the overall verdict is the
worst group, never the mean."""

from collections.abc import Iterator

from lgcov.judges import KPI, NodeState, Verdict
from lgcov.sampling import KpiStats, Sample, judge_samples, kpi_stats
from lgcov.slices import group_kpis, overall, render_groups

DROPS = KPI.binary("drops", "the answer keeps the order status", "Does it?")
GAINS = KPI.binary("gains", "the answer is polite", "Is it?")


class _Scripted:
    def __init__(self, kpi: KPI, script: list[str]) -> None:
        self.kpi = kpi
        self._script: Iterator[str] = iter(script)

    def judge(self, node_input: NodeState, node_output: NodeState) -> Verdict:
        label = next(self._script)
        return Verdict(self.kpi, label, self.kpi.score_map[label], 0.9, {})


def _cases(kpi: KPI, *scripts: list[str]) -> dict[str, tuple[Sample, ...]]:
    return {
        f"run-{i:02d}": judge_samples(_Scripted(kpi, s), {}, {}, len(s))
        for i, s in enumerate(scripts)
    }


def _stats(kpi: KPI, base: list[list[str]], new: list[list[str]]) -> KpiStats:
    return kpi_stats(kpi, len(base[0]), _cases(kpi, *base), _cases(kpi, *new))


def test_the_overall_verdict_is_the_worst_group_even_when_the_mean_passes() -> None:
    drop = _stats(DROPS, [["pass"]] * 20, [["fail"]] * 20)  # slice_a: every case worse
    gain = _stats(GAINS, [["fail"]] * 20, [["pass"]] * 20)  # slice_b: every case better
    where = {"drops": ("respond", "slice_a"), "gains": ("respond", "slice_b")}
    groups = group_kpis([drop, gain], where, ("node", "slice"))
    node = next(g for g in groups if g.slice == "")  # the node's total: the mean
    assert node.result is not None and node.result.delta == 0.0 and node.decision != "BLOCK"
    assert overall(groups) == ("fail", "slice `slice_a` at node `respond`")


def test_a_thin_slice_cannot_block_on_its_own() -> None:
    # Six cases, k = 3, all worse on the PR: significant (exact p = 2/64), but far too few
    # to detect a drop of the margin, 0.05, at power 0.8.
    base = [["pass"] * 3] * 6
    new = [["fail"] * 3] * 4 + [["fail", "fail", "pass"]] * 2
    stats = _stats(DROPS, base, new)
    assert stats.decision == "BLOCK"  # the KPI's own gate
    [group] = group_kpis([stats], {"drops": ("respond", "slice_a")}, ("node", "slice"))
    assert group.thin and group.decision == "INCONCLUSIVE"
    assert group.needed is not None and group.needed > 6
    assert f"⚠️ thin (n=6, need {group.needed})" in "\n".join(render_groups([group]))


def test_groups_render_one_table_per_node_in_a_stable_order() -> None:
    same = [["pass"]] * 4
    stats = [_stats(KPI.binary(n, "d", "r"), same, same) for n in ("k4", "k3", "k2", "k1")]
    where = {
        "k1": ("zeta", "b_mode"),
        "k2": ("alpha", "y_mode"),
        "k3": ("alpha", "a_mode"),
        "k4": ("zeta", "a_mode"),
    }
    lines = render_groups(group_kpis(stats, where, ("node", "slice")))
    titles = [line for line in lines if line.startswith("#### ")]
    assert titles == ["#### Node `alpha`", "#### Node `zeta`"]
    rows = [line.split(" | ")[0] for line in lines if line.startswith("| `")]
    assert rows == ["| `a_mode`", "| `y_mode`", "| `a_mode`", "| `b_mode`"]
