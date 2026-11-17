"""T151: the gate simulator (pure, seeded, offline)."""

import math

import pytest

from lgcov.config import GateConfig
from lgcov.gate_sim import (
    POWER_DELTAS,
    Cell,
    Resolved,
    Skipped,
    grid,
    grid_table,
    joint_cells,
    mc_error,
    predicted,
    resolve,
    run_grid,
    simulate,
    spawn_seeds,
    sweep,
)
from lgcov.stats import ni_required_n, required_n, rho_from_2x2, wilson


def test_joint_cells_keep_the_marginals_and_give_rho_back() -> None:
    p11, p10, p01, p00 = joint_cells(0.95, 0.95, 0.7)
    assert math.isclose(p11 + p10 + p01 + p00, 1.0)
    assert math.isclose(p11 + p10, 0.95) and math.isclose(p11 + p01, 0.95)
    assert rho_from_2x2(0.95, 0.95, p11) == pytest.approx(0.7)


def test_joint_cells_refuse_rho_07_at_base_095_and_pr_090() -> None:
    with pytest.raises(ValueError, match="impossible"):
        joint_cells(0.95, 0.90, 0.7)  # p11 would be 0.9008, over min(p_base, p_PR) = 0.90


def test_margin_and_alpha_default_to_the_gate_config() -> None:
    cell = Cell(n=100, rho=0.7, p_base=0.95)
    assert (cell.margin, cell.alpha) == (GateConfig().margin, GateConfig().alpha)


def test_clamp_uses_one_rho_in_both_arms() -> None:
    r = resolve(Cell(n=100, rho=0.7, p_base=0.95), rho_policy="clamp")
    assert not isinstance(r, Skipped)
    assert r.rho == pytest.approx(0.6882, abs=1e-4)
    assert r.clamped
    assert [d for d, _ in r.arms] == [0.0, pytest.approx(-0.05)]


def test_skip_policy_names_the_reason_instead_of_raising() -> None:
    r = resolve(Cell(n=100, rho=0.7, p_base=0.95), rho_policy="skip")
    assert isinstance(r, Skipped)
    assert "0.688" in r.reason and "p11" in r.reason


def test_a_pr_rate_below_zero_is_skipped() -> None:
    r = resolve(Cell(n=100, rho=0.3, p_base=0.03), rho_policy="clamp")
    assert isinstance(r, Skipped) and "PR rate" in r.reason


def test_spawned_seeds_are_reproducible_and_distinct() -> None:
    assert spawn_seeds(7, 100) == spawn_seeds(7, 100)
    assert len(set(spawn_seeds(7, 100))) == 100
    assert set(spawn_seeds(7, 100)).isdisjoint(spawn_seeds(8, 100))


def test_mc_error_is_the_binomial_se_and_the_wilson_interval() -> None:
    rate, se, ci = mc_error(50, 2000)
    assert rate == 0.025
    assert se == pytest.approx(math.sqrt(0.025 * 0.975 / 2000))
    assert ci == wilson(50, 2000)


def test_smoke_run_tallies_every_rep_and_is_reproducible() -> None:
    r = resolve(Cell(n=100, rho=0.7, p_base=0.95), rho_policy="clamp")
    assert not isinstance(r, Skipped)
    arms = simulate(r, reps=50, seed=0)
    assert arms == simulate(r, reps=50, seed=0)
    for arm in arms:
        assert arm.ship + arm.block + arm.inconclusive == 50
        assert arm.no_drop_ship <= arm.ship


def test_identical_sides_ship_through_the_no_drop_path() -> None:
    # rho = 1 at delta 0: no discordant pair, and no_drop_bound(100) = 0.037
    same = ((0.0, joint_cells(0.95, 0.95, 1.0)),)
    wide = Resolved(Cell(n=100, rho=1.0, p_base=0.95), rho=1.0, clamped=False, arms=same)
    assert [(a.ship, a.no_drop_ship) for a in simulate(wide, reps=20, seed=1)] == [(20, 20)]
    tight = Resolved(Cell(n=100, rho=1.0, p_base=0.95, margin=0.01), 1.0, False, same)
    assert [a.ship for a in simulate(tight, reps=20, seed=1)] == [0]  # 0.037 > 0.01


def test_sweep_skips_infeasible_cells_and_keeps_going() -> None:
    cells = [Cell(n=20, rho=0.7, p_base=0.95), Cell(n=20, rho=0.3, p_base=0.80)]
    out = sweep(cells, reps=5, seed=0, rho_policy="skip")
    assert isinstance(out[0], Skipped)
    assert not isinstance(out[1], Skipped) and len(out[1].arms) == 2


# T151 part 2: power arms, predicted power, the grid and its workers


def test_power_arms_keep_the_cells_rho_and_say_why_one_is_out_of_reach() -> None:
    r = resolve(Cell(n=100, rho=0.3, p_base=0.95), power=POWER_DELTAS)
    assert not isinstance(r, Skipped) and r.rho == 0.3
    (drop, cells), (gain, why) = r.power
    assert (drop, gain) == (-0.10, 0.05)
    assert not isinstance(cells, str) and isinstance(why, str)
    assert rho_from_2x2(0.95, 0.85, cells[0]) == pytest.approx(0.3)
    assert "PR rate 1 not in (0, 1)" in why


def test_the_clamped_cell_reaches_neither_power_arm() -> None:
    r = resolve(Cell(n=100, rho=0.7, p_base=0.95), rho_policy="clamp", power=POWER_DELTAS)
    assert not isinstance(r, Skipped)
    (_, drop), (_, gain) = r.power
    assert isinstance(drop, str) and "the reachable rho is 0.546" in drop
    assert isinstance(gain, str) and "PR rate 1" in gain


def test_power_arms_leave_the_error_arms_runs_as_they_were() -> None:
    cell = Cell(n=50, rho=0.3, p_base=0.80)
    plain, wide = resolve(cell), resolve(cell, power=POWER_DELTAS)
    assert not isinstance(plain, Skipped) and not isinstance(wide, Skipped)
    arms = simulate(wide, reps=20, seed=3)
    assert [a.delta for a in arms] == [0.0, pytest.approx(-0.05), -0.10, 0.05]
    assert arms[:2] == simulate(plain, reps=20, seed=3)


def test_predicted_power_is_the_gates_sizing_turned_around() -> None:
    n_block = required_n(0.80, 0.70, 0.3)  # BLOCK at Δ = -0.10: detection
    n_ship = ni_required_n(0.80, 0.80, 0.3, margin=0.05)  # SHIP at Δ = 0: non-inferiority
    assert n_block is not None and n_ship is not None
    block = predicted(Cell(n=n_block, rho=0.3, p_base=0.80), 0.3, -0.10)
    ship = predicted(Cell(n=n_ship, rho=0.3, p_base=0.80), 0.3, 0.0)
    assert block is not None and 0.80 <= block < 0.81
    assert ship is not None and 0.80 <= ship < 0.81
    assert predicted(Cell(n=100, rho=0.3, p_base=0.80), 0.3, -0.05) is None  # at the margin


def test_the_grid_is_m1_then_part_1s_clamped_cell_at_each_n() -> None:
    cells = grid()
    ns = (50, 100, 200, 800)
    m1 = {(n, rho, p) for p in (0.80, 0.95) for rho in (0.3, 0.7) for n in ns}
    assert {(c.n, c.rho, c.p_base) for c, policy in cells[:16] if policy == "skip"} == m1
    assert [(c.n, c.rho, c.p_base, policy) for c, policy in cells[16:]] == [
        (n, 0.7, 0.95, "clamp") for n in ns
    ]


def test_blocks_and_workers_never_change_the_result() -> None:
    plan = [
        resolve(Cell(n=20, rho=0.3, p_base=0.80), power=POWER_DELTAS),
        resolve(Cell(n=20, rho=0.7, p_base=0.95), power=POWER_DELTAS),
    ]
    one = run_grid(plan, reps=6, seed=0, workers=1, block=6)
    assert run_grid(plan, reps=6, seed=0, workers=2, block=2) == one
    first, second = one
    assert not isinstance(first, Skipped) and isinstance(second, Skipped)
    assert first.arms == simulate(first.resolved, reps=6, seed=0)


def test_grid_table_names_each_arms_calls_and_every_skip() -> None:
    plan = [
        resolve(Cell(n=20, rho=0.3, p_base=0.95), power=POWER_DELTAS),
        resolve(Cell(n=20, rho=0.7, p_base=0.95), power=POWER_DELTAS),
    ]
    text = grid_table(run_grid(plan, reps=4, seed=0))
    rows = [line for line in text.splitlines() if line.startswith("| 0.95 ")]
    assert len(rows) == 3  # Δ = 0, -m and -0.10; +0.05 is out of reach
    assert "| — | — | — |" in rows[1]  # at -m no decisive call is the right one
    assert "PR rate 1 not in (0, 1)" in text and "the reachable rho is 0.688" in text
