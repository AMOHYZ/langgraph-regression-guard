"""T151: how often decide() is wrong, by simulation. Pure: a cell in, decision counts out.

One cell is N paired binary cases (K = 1), a base pass rate p and a base-vs-PR correlation
rho (from the 2x2 table, as rho_from_2x2). Two arms: delta = 0, where BLOCK is wrong, and
delta = -margin, where SHIP is wrong. Each run draws N pairs and judges them the way the gate
does: paired_test, then decide() with one KPI (Holm with m = 1 is p <= alpha).

Both arms use one rho. A rho the PR arm's rates can't reach is skipped, or clamped to the
largest they allow (rho_policy), never changed for one arm only.
"""

from __future__ import annotations

import hashlib
import math
import random
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from lgcov.config import GateConfig
from lgcov.stats import decide, paired_test, wilson

_GATE = GateConfig()
RhoPolicy = Literal["skip", "clamp"]
Cells = tuple[float, float, float, float]  # both pass, base only, PR only, both fail


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


@dataclass(frozen=True)
class ArmResult:
    delta: float
    reps: int
    ship: int
    block: int
    inconclusive: int
    no_drop_ship: int  # SHIPs with no discordant pair: decide()'s no-drop path


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


def resolve(cell: Cell, rho_policy: RhoPolicy = "skip") -> Resolved | Skipped:
    """The cell's two arms with one rho, or why the cell can't run."""
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
    return Resolved(cell, rho, abs(rho - cell.rho) > 1e-9, arms)


def spawn_seeds(base: int, count: int) -> list[int]:
    """count independent 64-bit seeds from one base seed: SeedSequence.spawn without numpy."""
    return [
        int.from_bytes(hashlib.sha256(f"{base}:{i}".encode()).digest()[:8], "big")
        for i in range(count)
    ]


def _run(cell: Cell, cells: Cells, seed: int) -> tuple[str, bool]:
    data_seed, boot_seed = spawn_seeds(seed, 2)  # the bootstrap gets its own stream
    rng = random.Random(data_seed)
    p11, p10, p01, _ = cells
    base: list[list[float]] = []
    new: list[list[float]] = []
    for _ in range(cell.n):
        u = rng.random()
        both, base_only = u < p11, p11 <= u < p11 + p10
        pr_only = p11 + p10 <= u < p11 + p10 + p01
        pair = (1.0, 1.0) if both else (1.0, 0.0) if base_only else (0.0, float(pr_only))
        base.append([pair[0]])
        new.append([pair[1]])
    r = paired_test(base, new, alpha=cell.alpha, seed=boot_seed)
    decision = decide(
        r.delta,
        r.ci,
        cell.margin,
        r.p <= cell.alpha,
        discordant=r.discordant,
        n=r.n,
        alpha=cell.alpha,
    )
    return decision, r.discordant == 0


def simulate(resolved: Resolved, reps: int, seed: int = 0) -> tuple[ArmResult, ...]:
    """reps runs per arm; each arm's run seeds are spawned from that arm's own seed."""
    out = []
    arm_seeds = spawn_seeds(seed, len(resolved.arms))
    for (delta, cells), arm_seed in zip(resolved.arms, arm_seeds, strict=True):
        runs = [_run(resolved.cell, cells, s) for s in spawn_seeds(arm_seed, reps)]
        decisions = [d for d, _ in runs]
        out.append(
            ArmResult(
                delta=delta,
                reps=reps,
                ship=decisions.count("SHIP"),
                block=decisions.count("BLOCK"),
                inconclusive=decisions.count("INCONCLUSIVE"),
                no_drop_ship=sum(1 for d, none in runs if d == "SHIP" and none),
            )
        )
    return tuple(out)


def sweep(
    cells: Sequence[Cell], reps: int, seed: int = 0, rho_policy: RhoPolicy = "skip"
) -> list[CellResult | Skipped]:
    """Every cell that can run, in order; the others as Skipped with their reason."""
    out: list[CellResult | Skipped] = []
    for cell in cells:
        r = resolve(cell, rho_policy)
        out.append(r if isinstance(r, Skipped) else CellResult(r, simulate(r, reps, seed)))
    return out


def mc_error(k: int, reps: int) -> tuple[float, float, tuple[float, float]]:
    """A Monte Carlo rate, its binomial SE and its Wilson 95% interval."""
    rate = k / reps
    return rate, math.sqrt(rate * (1 - rate) / reps), wilson(k, reps)


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


def main(argv: Sequence[str]) -> int:
    """python -m lgcov.gate_sim [reps] [seed]: T151's one cell, N=100, rho=0.7, p=0.95."""
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
