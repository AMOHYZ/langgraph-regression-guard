"""T151: how often decide() is wrong, by simulation. Pure: a cell in, decision counts out.

One cell is N paired binary cases (K = 1), a base pass rate p and a base-vs-PR correlation
rho (from the 2x2 table, as rho_from_2x2). Two arms: delta = 0, where BLOCK is wrong, and
delta = -margin, where SHIP is wrong. Each run draws N pairs and judges them the way the gate
does: paired_test, then decide() with one KPI (Holm with m = 1 is p <= alpha).

Both arms use one rho. A rho the PR arm's rates can't reach is skipped, or clamped to the
largest they allow (rho_policy), never changed for one arm only.

Part 2 adds power arms (POWER_DELTAS), each run at the cell's rho or skipped with its reason,
and runs the M1 grid (grid()) over a process pool. Runs are seeded by index, so the result
doesn't depend on the number of workers. A Scenario loses pairs (at random, or only the PR's
failures) or adds KPIs; every run is decided by the gate's own gate_kpis (Holm across the
KPIs) and gate_findings (the PR's verdict). With one KPI, each run also puts Tango's and the
SCAS-style lower bound (paired_score) next to the gate's (bounds_table). A graded Scenario
(k > 1) draws graded_cases, k samples per case scored 0, 0.5 or 1, so paired_test takes its
sign-flip path.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import random
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from statistics import NormalDist
from typing import Literal

from lgcov.config import GateConfig
from lgcov.paired_score import skew_corrected_ci, tango_ci
from lgcov.sampling import KpiStats, gate_kpis
from lgcov.stats import Decision, PairedResult, decide, no_drop_bound, paired_test, sigma_d, wilson
from lgcov.verdict_actions import gate_findings

_GATE = GateConfig()
RhoPolicy = Literal["skip", "clamp"]
Cells = tuple[float, float, float, float]  # both pass, base only, PR only, both fail
POWER_DELTAS = (-0.10, 0.05)  # T151 part 2: BLOCK is right at -0.10, SHIP at +0.05
GRID_N = (50, 100, 200, 800)
GRADED_SIGMA = 1.0  # the spread of cases' latent effects in the graded model, probit scale


@dataclass(frozen=True)
class Scenario:
    """What a run's data look like beyond part 1's one binary KPI with every pair."""

    name: str = "binary, one KPI, every pair"
    kpis: int = 1  # blocking KPIs on the same cases: KPI 1 takes the arm's delta, the rest 0
    missing: float = 0.0  # the chance a pair is lost
    informative: bool = False  # lose only pairs whose PR result fails, as a PR's bad outputs can
    k: int = 1  # samples per case; above 1, graded scores (graded_cases), the sign-flip path

    def __post_init__(self) -> None:
        if self.k > 1 and (self.kpis > 1 or self.missing):
            raise ValueError("a graded scenario here has one KPI and every pair")


MAIN = Scenario()
SCENARIOS = (
    Scenario("10% of pairs lost at random", missing=0.10),
    Scenario("half of the PR's failing results lost", missing=0.5, informative=True),
    Scenario("2 KPIs, Holm", kpis=2),
    Scenario("4 KPIs, Holm", kpis=4),
    Scenario("graded, K=3 samples per case: the sign-flip path", k=3),
)


@dataclass(frozen=True)
class Cell:
    n: int
    rho: float
    p_base: float
    margin: float = _GATE.margin
    alpha: float = _GATE.alpha


@dataclass(frozen=True)
class Skipped:
    cell: Cell
    reason: str


@dataclass(frozen=True)
class Resolved:
    cell: Cell
    rho: float  # the rho both arms use
    clamped: bool  # True when rho is below cell.rho because the PR arm can't reach it
    arms: tuple[tuple[float, Cells], ...]  # (delta, joint cells) per arm
    power: tuple[tuple[float, Cells | str], ...] = ()  # power arms: cells, or why rho can't


@dataclass(frozen=True)
class ArmResult:
    delta: float
    reps: int
    ship: int
    block: int
    inconclusive: int
    no_drop_ship: int  # SHIPs with no discordant pair: decide()'s no-drop path
    lost: int = 0  # pairs lost to missing results, over all runs and KPIs
    first_block: int = 0  # runs that BLOCKed KPI 1, the one at the arm's delta
    other_block: int = 0  # runs that BLOCKed a KPI at delta 0: a false alarm
    # One KPI only: the calls with Tango's and the SCAS-style CI in place of BCa, and how often
    # each lower bound sat above the true delta (the gate's, Tango's, the SCAS-style one)
    tango: tuple[int, int, int] = (0, 0, 0)  # SHIP / BLOCK / INCONCLUSIVE
    scas: tuple[int, int, int] = (0, 0, 0)
    misses: tuple[int, int, int] = (0, 0, 0)
    rho_sum: float = 0.0  # graded: the observed correlation of case means, over all runs


Bounds = tuple[Decision, Decision, tuple[bool, bool, bool]]  # Tango's call, SCAS-style, misses


@dataclass(frozen=True)
class Run:
    decision: Decision  # the PR's verdict, as gate_findings gives it
    no_drop: bool  # KPI 1 had no discordant pair
    lost: int
    first_block: bool
    other_block: bool
    bounds: Bounds | None = None  # one KPI only
    rho: float = 0.0  # graded: the observed correlation of case means


@dataclass(frozen=True)
class CellResult:
    resolved: Resolved
    arms: tuple[ArmResult, ...]


def _rho_range(p_a: float, p_b: float) -> tuple[float, float]:
    """The rhos a 2x2 table with these margins allows: p11 in [max(0, p_a + p_b - 1), min]."""
    pab, spread = p_a * p_b, math.sqrt(p_a * (1 - p_a) * p_b * (1 - p_b))
    return ((max(0.0, p_a + p_b - 1) - pab) / spread, (min(p_a, p_b) - pab) / spread)


def joint_cells(p_a: float, p_b: float, rho: float) -> Cells:
    """The base-vs-PR 2x2 table with pass rates p_a (base), p_b (PR) and correlation rho."""
    lo, hi = _rho_range(p_a, p_b)
    if not lo - 1e-9 <= rho <= hi + 1e-9:
        raise ValueError(f"impossible rho={rho} at p_base={p_a}, p_PR={p_b}: [{lo:.3f}, {hi:.3f}]")
    p11 = p_a * p_b + rho * math.sqrt(p_a * (1 - p_a) * p_b * (1 - p_b))
    p11 = min(max(p11, max(0.0, p_a + p_b - 1)), min(p_a, p_b))
    return (p11, p_a - p11, p_b - p11, 1 - p_a - p_b + p11)


def _power_arm(cell: Cell, rho: float, delta: float) -> Cells | str:
    """A power arm's 2x2 table at the cell's rho, or why that rho can't reach it."""
    p_pr = cell.p_base + delta
    if not 0 < p_pr < 1:
        return f"PR rate {p_pr:g} not in (0, 1)"
    lo, hi = _rho_range(cell.p_base, p_pr)
    if lo - 1e-9 <= rho <= hi + 1e-9:
        return joint_cells(cell.p_base, p_pr, rho)
    bound = min(cell.p_base, p_pr) if rho > hi else max(0.0, cell.p_base + p_pr - 1)
    return (
        f"rho={rho:.3f} impossible at p_base={cell.p_base:g}, PR={p_pr:g}: needs p11 "
        f"{'<=' if rho > hi else '>='} {bound:g}; the reachable rho is {min(max(rho, lo), hi):.3f}"
    )


def resolve(
    cell: Cell, rho_policy: RhoPolicy = "skip", power: Sequence[float] = ()
) -> Resolved | Skipped:
    """The cell's two arms with one rho, or why the cell can't run. Each power arm runs at
    that rho or carries the reason it can't; it never changes the rho."""
    deltas = (0.0, -cell.margin)
    rates = [cell.p_base + d for d in deltas]
    if not all(0 < p < 1 for p in rates):
        return Skipped(cell, f"PR rate {rates[-1]:g} or base {cell.p_base:g} not in (0, 1)")
    lo = max(_rho_range(cell.p_base, p)[0] for p in rates)
    hi = min(_rho_range(cell.p_base, p)[1] for p in rates)
    rho = min(max(cell.rho, lo), hi)
    if abs(rho - cell.rho) > 1e-9 and rho_policy == "skip":
        p_pr = rates[-1]
        bound = min(cell.p_base, p_pr) if cell.rho > hi else max(0.0, cell.p_base + p_pr - 1)
        return Skipped(
            cell,
            f"rho={cell.rho:g} impossible at p_base={cell.p_base:g}, PR={p_pr:g}: needs "
            f"p11 {'<=' if cell.rho > hi else '>='} {bound:g}; the reachable rho is {rho:.3f}",
        )
    arms = tuple((d, joint_cells(cell.p_base, p, rho)) for d, p in zip(deltas, rates, strict=True))
    reach = tuple((d, _power_arm(cell, rho, d)) for d in power)
    return Resolved(cell, rho, abs(rho - cell.rho) > 1e-9, arms, reach)


_NO_TABLE: Cells = (0.0, 0.0, 0.0, 0.0)  # the graded model draws no 2x2 table


def resolve_graded(cell: Cell, power: Sequence[float] = ()) -> Resolved | Skipped:
    """The graded model's arms. It has no 2x2 table, so any rho in [-1, 1] runs as asked; an
    arm needs only a PR rate in (0, 1)."""

    def arm(delta: float) -> Cells | str:
        p_pr = cell.p_base + delta
        return _NO_TABLE if 0 < p_pr < 1 else f"PR rate {p_pr:g} not in (0, 1)"

    why = [w for w in (arm(0.0), arm(-cell.margin)) if isinstance(w, str)]
    if not -1 <= cell.rho <= 1:
        why.append(f"rho={cell.rho:g} not in [-1, 1]")
    if why:
        return Skipped(cell, why[0])
    arms = ((0.0, _NO_TABLE), (-cell.margin, _NO_TABLE))
    return Resolved(cell, cell.rho, False, arms, tuple((d, arm(d)) for d in power))


def graded_cases(
    rng: random.Random, p_base: float, delta: float, rho: float, n: int, k: int
) -> tuple[list[list[float]], list[list[float]]]:
    """n cases of k samples a side, each scored 0, 0.5 or 1. A case's pass probability is
    Φ(a + σz) on each side, its base and PR z's correlated rho, and each sample is
    Binomial(2, that probability) / 2. a is set so the mean score is exactly p_base on the base
    and p_base + delta on the PR, since E[Φ(a + σZ)] = Φ(a / √(1 + σ²))."""
    scale = math.sqrt(1 + GRADED_SIGMA**2)
    a_base, a_pr = (_NORMAL.inv_cdf(p) * scale for p in (p_base, p_base + delta))
    base: list[list[float]] = []
    new: list[list[float]] = []
    for _ in range(n):
        z_base = rng.gauss(0.0, 1.0)
        z_pr = rho * z_base + math.sqrt(1 - rho * rho) * rng.gauss(0.0, 1.0)
        for a, z, side in ((a_base, z_base, base), (a_pr, z_pr, new)):
            pi = _NORMAL.cdf(a + GRADED_SIGMA * z)
            side.append([((rng.random() < pi) + (rng.random() < pi)) / 2 for _ in range(k)])
    return base, new


def spawn_seeds(base: int, count: int) -> list[int]:
    """count independent 64-bit seeds from one base seed: SeedSequence.spawn without numpy."""
    return [
        int.from_bytes(hashlib.sha256(f"{base}:{i}".encode()).digest()[:8], "big")
        for i in range(count)
    ]


def _pairs(rng: random.Random, cells: Cells, n: int) -> list[tuple[float, float]]:
    """n (base, PR) results from the 2x2 table, one uniform each."""
    p11, p10, p01, _ = cells
    out = []
    for _ in range(n):
        u = rng.random()
        both, base_only = u < p11, p11 <= u < p11 + p10
        pr_only = p11 + p10 <= u < p11 + p10 + p01
        out.append((1.0, 1.0) if both else (1.0, 0.0) if base_only else (0.0, float(pr_only)))
    return out


def _lost(rng: random.Random, pair: tuple[float, float], scenario: Scenario) -> bool:
    """One uniform per pair, so the at-random and the informative arms lose on the same draws."""
    u = rng.random()
    return u < scenario.missing and (not scenario.informative or pair[1] == 0.0)


def _kpi(name: str, r: PairedResult | None, cell: Cell) -> KpiStats:
    """A blocking KPI as gate_kpis takes it: this run's paired result and a sure judge."""
    return KpiStats(
        kpi=name,
        n=r.n if r else 0,
        k=1,
        pass_at_k=0.0,
        pass_hat_k=0.0,
        infra=0,
        outcome="needs_human",
        paired=r,
        ms_total=0.0,
        ms_mean=0.0,
        tokens_total=0,
        tokens_mean=0.0,
        flaky_cases=(),
        confidence=1.0,
        margin=cell.margin,
    )


def _bounds(cell: Cell, delta: float, r: PairedResult, kept: list[tuple[float, float]]) -> Bounds:
    """decide() with Tango's and then the SCAS-style CI in place of BCa, with the same McNemar
    test and no-drop path, and whether each 95% lower bound sits above the true delta. The
    gate's own bound is BCa's, or the no-drop bound when no pair is discordant."""
    table = [kept.count(pair) for pair in ((1.0, 1.0), (1.0, 0.0), (0.0, 1.0), (0.0, 0.0))]
    tango, scas = tango_ci(*table), skew_corrected_ci(*table)  # both pass, base only, PR only
    significant = r.p <= cell.alpha  # Holm with one KPI
    calls = [
        decide(
            r.delta, ci, cell.margin, significant, discordant=r.discordant, n=r.n, alpha=cell.alpha
        )
        for ci in (tango, scas)
    ]
    gate_lo = -no_drop_bound(r.n, cell.alpha) if r.discordant == 0 else r.ci[0]
    return calls[0], calls[1], (gate_lo > delta, tango[0] > delta, scas[0] > delta)


def _run(cell: Cell, delta: float, cells: Cells, null: Cells, seed: int, scenario: Scenario) -> Run:
    """Each KPI's N pairs (KPI 1 from the arm's table, the rest from delta 0's), less the lost
    ones, judged by the gate's own paired_test, then gate_kpis and gate_findings. With one KPI,
    also its calls and misses under Tango's and the SCAS-style bound. Graded: N cases of k
    samples (graded_cases) at the cell's rho, so paired_test takes its sign-flip path."""
    if scenario.k > 1:
        data_seed, boot_seed = spawn_seeds(seed, 2)
        rng = random.Random(data_seed)
        base, new = graded_cases(rng, cell.p_base, delta, cell.rho, cell.n, scenario.k)
        g = paired_test(base, new, alpha=cell.alpha, seed=boot_seed)
        gated = gate_kpis([_kpi("kpi1", g, cell)], alpha=cell.alpha)
        verdict = gate_findings(gated).statistical or "INCONCLUSIVE"
        return Run(verdict, g.discordant == 0, 0, verdict == "BLOCK", False, rho=g.rho or 0.0)
    seeds = spawn_seeds(seed, 3 + 2 * (scenario.kpis - 1))  # data, bootstrap, losses, more KPIs
    losses = random.Random(seeds[2])
    streams = [(seeds[0], seeds[1]), *zip(seeds[3::2], seeds[4::2], strict=True)]
    stats, lost, no_drop, bounds = [], 0, False, None
    for j, (data_seed, boot_seed) in enumerate(streams):
        pairs = _pairs(random.Random(data_seed), cells if j == 0 else null, cell.n)
        kept = [pair for pair in pairs if not _lost(losses, pair, scenario)]
        lost += len(pairs) - len(kept)
        r = None
        if kept:
            base, new = [[b] for b, _ in kept], [[p] for _, p in kept]
            # T151 measured the gate's BCa interval; binary KPIs now read Tango's, so the
            # runs ask for BCa to keep part 1's and part 2's tables reproducible
            r = paired_test(base, new, alpha=cell.alpha, seed=boot_seed, interval="bca")
        no_drop = no_drop or (j == 0 and r is not None and r.discordant == 0)
        if scenario.kpis == 1 and r is not None and len(kept) > 1:
            bounds = _bounds(cell, delta, r, kept)
        stats.append(_kpi(f"kpi{j + 1}", r, cell))
    gated = gate_kpis(stats, alpha=cell.alpha)
    verdict = gate_findings(gated).statistical or "INCONCLUSIVE"
    blocked = [g.decision == "BLOCK" for g in gated]
    return Run(verdict, no_drop, lost, blocked[0], any(blocked[1:]), bounds)


def arms_to_run(resolved: Resolved, seed: int) -> list[tuple[float, Cells, int]]:
    """(delta, joint cells, arm seed) for each arm that can run: the error arms, then the power
    arms in reach. Seeds go by slot, error arms first, so power arms never move their runs."""
    slots = [*resolved.arms, *resolved.power]
    seeds = spawn_seeds(seed, len(slots))
    return [(d, c, s) for (d, c), s in zip(slots, seeds, strict=True) if not isinstance(c, str)]


def simulate_arm(
    cell: Cell,
    delta: float,
    cells: Cells,
    null: Cells,
    arm_seed: int,
    start: int,
    stop: int,
    scenario: Scenario = MAIN,
) -> ArmResult:
    """Runs start to stop - 1 of one arm, so a pool can cut an arm into blocks. null is delta
    0's table, which KPIs after the first draw from."""
    seeds = spawn_seeds(arm_seed, stop)[start:stop]
    runs = [_run(cell, delta, cells, null, s, scenario) for s in seeds]
    verdicts = [run.decision for run in runs]
    bounds = [run.bounds for run in runs if run.bounds is not None]
    return ArmResult(
        delta=delta,
        reps=stop - start,
        ship=verdicts.count("SHIP"),
        block=verdicts.count("BLOCK"),
        inconclusive=verdicts.count("INCONCLUSIVE"),
        no_drop_ship=sum(1 for run in runs if run.decision == "SHIP" and run.no_drop),
        lost=sum(run.lost for run in runs),
        first_block=sum(run.first_block for run in runs),
        other_block=sum(run.other_block for run in runs),
        tango=_calls([b[0] for b in bounds]),
        scas=_calls([b[1] for b in bounds]),
        misses=_add([tuple(map(int, b[2])) for b in bounds]),
        rho_sum=sum(run.rho for run in runs),
    )


def _calls(decisions: Sequence[Decision]) -> tuple[int, int, int]:
    return decisions.count("SHIP"), decisions.count("BLOCK"), decisions.count("INCONCLUSIVE")


def _add(counts: Sequence[tuple[int, ...]]) -> tuple[int, int, int]:
    return (sum(c[0] for c in counts), sum(c[1] for c in counts), sum(c[2] for c in counts))


def merge(parts: Sequence[ArmResult]) -> ArmResult:
    """One arm's blocks, added up."""
    return ArmResult(
        delta=parts[0].delta,
        reps=sum(p.reps for p in parts),
        ship=sum(p.ship for p in parts),
        block=sum(p.block for p in parts),
        inconclusive=sum(p.inconclusive for p in parts),
        no_drop_ship=sum(p.no_drop_ship for p in parts),
        lost=sum(p.lost for p in parts),
        first_block=sum(p.first_block for p in parts),
        other_block=sum(p.other_block for p in parts),
        tango=_add([p.tango for p in parts]),
        scas=_add([p.scas for p in parts]),
        misses=_add([p.misses for p in parts]),
        rho_sum=sum(p.rho_sum for p in parts),
    )


def simulate(
    resolved: Resolved, reps: int, seed: int = 0, scenario: Scenario = MAIN
) -> tuple[ArmResult, ...]:
    """reps runs per arm; each arm's run seeds are spawned from that arm's own seed."""
    null = resolved.arms[0][1]
    return tuple(
        simulate_arm(resolved.cell, delta, cells, null, arm_seed, 0, reps, scenario)
        for delta, cells, arm_seed in arms_to_run(resolved, seed)
    )


def sweep(
    cells: Sequence[Cell], reps: int, seed: int = 0, rho_policy: RhoPolicy = "skip"
) -> list[CellResult | Skipped]:
    """Every cell that can run, in order; the others as Skipped with their reason."""
    out: list[CellResult | Skipped] = []
    for cell in cells:
        r = resolve(cell, rho_policy)
        out.append(r if isinstance(r, Skipped) else CellResult(r, simulate(r, reps, seed)))
    return out


def grid() -> list[tuple[Cell, RhoPolicy]]:
    """The M1 grid, which skips a cell its rho can't reach, then part 1's own cell (p 0.95, rho
    clamped from 0.7) at each N, as a labelled extra row."""
    m1: list[tuple[Cell, RhoPolicy]] = [
        (Cell(n=n, rho=rho, p_base=p), "skip")
        for p in (0.80, 0.95)
        for rho in (0.3, 0.7)
        for n in GRID_N
    ]
    return m1 + [(Cell(n=n, rho=0.7, p_base=0.95), "clamp") for n in GRID_N]


@dataclass(frozen=True)
class Block:
    """A slice of one arm's runs: what one worker runs at a time."""

    key: tuple[int, int]  # (cell, arm) it adds to
    cell: Cell
    delta: float
    cells: Cells
    null: Cells
    arm_seed: int
    start: int
    stop: int
    scenario: Scenario


def _run_block(b: Block) -> ArmResult:
    return simulate_arm(b.cell, b.delta, b.cells, b.null, b.arm_seed, b.start, b.stop, b.scenario)


def run_grid(
    plan: Sequence[Resolved | Skipped],
    reps: int,
    seed: int = 0,
    workers: int = 1,
    block: int = 250,
    scenario: Scenario = MAIN,
) -> list[CellResult | Skipped]:
    """simulate() for every resolved cell, its arms cut into blocks of runs. With workers > 1
    the blocks run in a process pool. Each run's seed comes from its index, so the result is
    the same for any number of workers and any block size."""
    blocks = [
        Block(
            (i, j),
            r.cell,
            delta,
            cells,
            r.arms[0][1],
            arm_seed,
            start,
            min(start + block, reps),
            scenario,
        )
        for i, r in enumerate(plan)
        if not isinstance(r, Skipped)
        for j, (delta, cells, arm_seed) in enumerate(arms_to_run(r, seed))
        for start in range(0, reps, block)
    ]
    blocks.sort(key=lambda b: -b.cell.n)  # the longest first, so the workers end together
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            parts = list(pool.map(_run_block, blocks))
    else:
        parts = [_run_block(b) for b in blocks]
    by_arm: dict[tuple[int, int], list[ArmResult]] = {}
    for b, part in zip(blocks, parts, strict=True):
        by_arm.setdefault(b.key, []).append(part)
    out: list[CellResult | Skipped] = []
    for i, r in enumerate(plan):
        if isinstance(r, Skipped):
            out.append(r)
            continue
        arms = range(len(arms_to_run(r, seed)))
        out.append(CellResult(r, tuple(merge(by_arm[(i, j)]) for j in arms)))
    return out


def mc_error(k: int, reps: int) -> tuple[float, float, tuple[float, float]]:
    """A Monte Carlo rate, its binomial SE and its Wilson 95% interval."""
    rate = k / reps
    return rate, math.sqrt(rate * (1 - rate) / reps), wilson(k, reps)


_NORMAL = NormalDist()


def _power(cell: Cell, rho: float, delta: float, gap: float, alpha: float) -> float:
    se = sigma_d(cell.p_base, cell.p_base + delta, rho) / math.sqrt(cell.n)
    return 1.0 if se <= 0 else _NORMAL.cdf(gap / se - _NORMAL.inv_cdf(1 - alpha / 2))


def predicted(cell: Cell, rho: float, delta: float, kpis: int = 1) -> float | None:
    """The chance of the right call by the normal approximation the gate sizes N with: SHIP
    above -margin (ni_required_n's), BLOCK below it (required_n's, detection). None at the
    margin, where neither decisive call is the right one. With more KPIs, all but KPI 1 at
    delta 0: BLOCK is KPI 1's at alpha / kpis, as the gate sizes N*, and SHIP needs them all."""
    if abs(delta + cell.margin) < 1e-9:
        return None
    if delta < -cell.margin:
        return _power(cell, rho, delta, -delta, cell.alpha / kpis)
    rest = _power(cell, rho, 0.0, cell.margin, cell.alpha) ** (kpis - 1)
    return _power(cell, rho, delta, delta + cell.margin, cell.alpha) * rest


def _pct(x: float) -> str:
    return f"{100 * x:.2f}%"


def table(result: CellResult, seed: int) -> str:
    """The Markdown table for one cell: the wrong decision per arm, with its MC error."""
    r, c = result.resolved, result.resolved.cell
    rho = f"{r.rho:.3f}" + (f" (clamped from {c.rho:g})" if r.clamped else "")
    lines = [
        f"N={c.n}, p_base={c.p_base:g}, rho={rho}, margin m={c.margin:g}, alpha={c.alpha:g}, "
        f"R={result.arms[0].reps} per arm, seed={seed}",
        "",
        "| Δ | Wrong call | Rate | MC SE | Wilson 95% | No-drop SHIPs (share of SHIPs) "
        "| No-drop SHIP rate | SHIP / BLOCK / INCONCLUSIVE |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for a in result.arms:
        wrong, k = ("BLOCK", a.block) if a.delta == 0 else ("SHIP", a.ship)
        rate, se, (lo, hi) = mc_error(k, a.reps)
        share = f"{a.no_drop_ship} ({_pct(a.no_drop_ship / a.ship)})" if a.ship else "0"
        lines.append(
            f"| {a.delta:+g} | {wrong} | {_pct(rate)} | {_pct(se)} | {_pct(lo)}–{_pct(hi)} "
            f"| {share} | {_pct(a.no_drop_ship / a.reps)} "
            f"| {a.ship} / {a.block} / {a.inconclusive} |"
        )
    return "\n".join(lines)


def grid_table(results: Sequence[CellResult | Skipped], scenario: Scenario = MAIN) -> str:
    """One Markdown row per arm: its calls, the wrong call's rate with its MC error, and the
    right call's rate next to the predicted one. Skipped cells and arms follow the table.
    With lost pairs, the share lost; with more KPIs, how often one at delta 0 was BLOCKed.
    Graded: no predicted power (its formulas are binary), and the observed rho of case means."""
    extra = (["Pairs lost"] if scenario.missing else []) + (
        ["BLOCK on a KPI at Δ=0 (Wilson 95%)"] if scenario.kpis > 1 else []
    )
    extra += ["Observed ρ of case means"] if scenario.k > 1 else []
    head = ["p", "ρ", "N", "Δ", "SHIP / BLOCK / INCONCLUSIVE", "No-drop SHIPs", "Wrong call"]
    head += ["Rate (MC SE)", "Wilson 95%", "Right call", "Rate", "Predicted", *extra]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    skips = []
    for out in results:
        c = out.cell if isinstance(out, Skipped) else out.resolved.cell
        where = f"N={c.n}, ρ={c.rho:g}, p={c.p_base:g}"
        if isinstance(out, Skipped):
            skips.append(f"- Skipped {where}: {out.reason}")
            continue
        r = out.resolved
        rho = f"{r.rho:.3f}" + (f" (clamped from {c.rho:g})" if r.clamped else "")
        for a in out.arms:
            wrong, k = ("SHIP", a.ship) if a.delta < -c.margin + 1e-9 else ("BLOCK", a.block)
            rate, se, (lo, hi) = mc_error(k, a.reps)
            right = "— | — | —"
            p = predicted(c, r.rho, a.delta, scenario.kpis)
            if p is not None:
                name, n_right = (
                    ("SHIP", a.ship) if a.delta > -c.margin else ("BLOCK", a.first_block)
                )
                guess = "—" if scenario.k > 1 else _pct(p)
                right = f"{name} | {_pct(n_right / a.reps)} | {guess}"
            more = [_pct(a.lost / (a.reps * c.n * scenario.kpis))] if scenario.missing else []
            if scenario.kpis > 1:
                rate_other, _, (lo_o, hi_o) = mc_error(a.other_block, a.reps)
                more.append(f"{_pct(rate_other)} ({_pct(lo_o)}–{_pct(hi_o)})")
            more += [f"{a.rho_sum / a.reps:.3f}"] if scenario.k > 1 else []
            lines.append(
                f"| {c.p_base:g} | {rho} | {c.n} | {a.delta:+g} "
                f"| {a.ship} / {a.block} / {a.inconclusive} | {a.no_drop_ship} | {wrong} "
                f"| {_pct(rate)} ({_pct(se)}) | {_pct(lo)}–{_pct(hi)} | {right} |"
                + "".join(f" {m} |" for m in more)
            )
        skips += [
            f"- Skipped arm Δ={d:+g} at {where}: {w}" for d, w in r.power if isinstance(w, str)
        ]
    return "\n".join([*lines, "", *skips] if skips else lines)


def bounds_table(results: Sequence[CellResult | Skipped]) -> str:
    """One row per arm: how often each 95% lower bound sat above the true delta (nominal 2.5%),
    with its Wilson interval, and decide()'s SHIPs and BLOCKs with each CI."""
    head = "| p | ρ | N | Δ | Gate's bound above Δ | Tango's | SCAS-style "
    lines = [head + "| SHIP: gate / Tango / SCAS-style | BLOCK: gate / Tango / SCAS-style |"]
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for out in results:
        if isinstance(out, Skipped):
            continue
        r, c = out.resolved, out.resolved.cell
        rho = f"{r.rho:.3f}" + (f" (clamped from {c.rho:g})" if r.clamped else "")
        for a in out.arms:
            misses = []
            for k in a.misses:
                rate, _, (lo, hi) = mc_error(k, a.reps)
                misses.append(f"{_pct(rate)} ({_pct(lo)}–{_pct(hi)})")
            ships = " / ".join(_pct(x / a.reps) for x in (a.ship, a.tango[0], a.scas[0]))
            blocks = " / ".join(_pct(x / a.reps) for x in (a.block, a.tango[1], a.scas[1]))
            lines.append(
                f"| {c.p_base:g} | {rho} | {c.n} | {a.delta:+g} | {' | '.join(misses)} "
                f"| {ships} | {blocks} |"
            )
    return "\n".join(lines)


def _grid_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m lgcov.gate_sim grid")
    parser.add_argument("reps", type=int, nargs="?", default=5000, help="runs per arm")
    parser.add_argument("seed", type=int, nargs="?", default=0, help="base seed")
    parser.add_argument("--workers", type=int, default=1, help="worker processes")
    parser.add_argument(
        "--scenario-reps", type=int, default=1000, help="runs per arm for the other scenarios"
    )
    args = parser.parse_args(argv)
    reps: int = args.reps
    seed: int = args.seed
    workers: int = args.workers
    scenario_reps: int = args.scenario_reps
    plan = [resolve(cell, policy, POWER_DELTAS) for cell, policy in grid()]
    # Graded: no 2x2 table, so no clamped row; p=0.95 with rho=0.7 runs as asked
    graded = [resolve_graded(cell, POWER_DELTAS) for cell, policy in grid() if policy == "skip"]
    for what, cells in (("binary", plan), ("graded", graded)):  # the logged reasons, up front
        for r in cells:
            if isinstance(r, Skipped):
                print(f"{what}: skipped {r.cell}: {r.reason}", file=sys.stderr)
                continue
            for d, why in r.power:
                if isinstance(why, str):
                    print(f"{what}: skipped arm delta={d:+g} of {r.cell}: {why}", file=sys.stderr)
    print(f"M1 grid: seed={seed}, margin m={_GATE.margin:g}, alpha={_GATE.alpha:g}", flush=True)
    for scenario, r_ in ((MAIN, reps), *((s, scenario_reps) for s in SCENARIOS)):
        start = time.perf_counter()
        results = run_grid(graded if scenario.k > 1 else plan, r_, seed, workers, scenario=scenario)
        took = time.perf_counter() - start
        print(f"\n## {scenario.name}: R={r_} per arm, {took:.0f} s on {workers} workers\n")
        print(grid_table(results, scenario), flush=True)
        if scenario == MAIN:
            print("\n## Lower bounds: the gate's BCa, Tango's, the SCAS-style one\n")
            print(bounds_table(results), flush=True)
        print(f"{scenario.name}: R={r_}, {workers} workers, {took:.1f} s", file=sys.stderr)
    return 0


def main(argv: Sequence[str]) -> int:
    """python -m lgcov.gate_sim [reps] [seed]: T151's one cell, N=100, rho=0.7, p=0.95.
    python -m lgcov.gate_sim grid [reps] [seed] [--workers W]: part 2's grid."""
    if argv and argv[0] == "grid":
        return _grid_main(argv[1:])
    reps = int(argv[0]) if argv else 2000
    seed = int(argv[1]) if len(argv) > 1 else 0
    for out in sweep([Cell(n=100, rho=0.7, p_base=0.95)], reps, seed, rho_policy="clamp"):
        if isinstance(out, Skipped):
            print(f"skipped {out.cell}: {out.reason}", file=sys.stderr)
        else:
            print(table(out, seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
