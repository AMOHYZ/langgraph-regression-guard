"""T105: the speed benchmark behind `make bench-speed`.

It runs the demo PR (PR #7's one-line prompt change) through `lgcov ci` n times with
unchanged nodes served and n times with `--live`, each run in a fresh process, the modes
alternating after one uncounted warm-up run. It writes a markdown table of what it
measured: median (min–max) per measure, and nothing else.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from statistics import median
from time import perf_counter

# PR #7, the demo PR: the prompt change that makes `respond` drift.
PROMPT = ('"You are a concise support agent."', '"You are a friendly, detailed support agent."')
CI_ARGS = (
    *("ci", "--report", "report.md", "--graph", "graph.py:graph", "--cassette-dir", "cassettes"),
    *("--taxonomy", "taxonomy.yaml", "--timing-out", "timing.json"),
)
MODES: tuple[tuple[str, tuple[str, ...]], ...] = (("served", ()), ("--live", ("--live",)))


@dataclass(frozen=True)
class Sample:
    """One `lgcov ci` run: wall-clock around its process; the rest from its own timing."""

    wall: float
    agent: float
    judges: float
    live: int
    served: int
    judge_calls: int
    tokens: int | None = None  # None: no judge reported usage


@dataclass(frozen=True)
class Spread:
    median: float
    low: float
    high: float


def spread(values: Sequence[float]) -> Spread:
    return Spread(median(values), min(values), max(values))


@dataclass(frozen=True)
class Row:
    mode: str
    runs: int
    wall: Spread
    agent: Spread
    judges: Spread
    live: Spread
    served: Spread
    judge_calls: Spread
    tokens: Spread | None  # None: no run reported tokens


def aggregate(mode: str, samples: Sequence[Sample]) -> Row:
    """Median, min and max of each measure over one mode's runs (pure)."""
    tokens = [float(s.tokens) for s in samples if s.tokens is not None]
    return Row(
        mode=mode,
        runs=len(samples),
        wall=spread([s.wall for s in samples]),
        agent=spread([s.agent for s in samples]),
        judges=spread([s.judges for s in samples]),
        live=spread([float(s.live) for s in samples]),
        served=spread([float(s.served) for s in samples]),
        judge_calls=spread([float(s.judge_calls) for s in samples]),
        tokens=spread(tokens) if tokens else None,
    )


def _seconds(x: float) -> str:
    return f"{x * 1000:.1f} ms" if x < 1 else f"{x:.2f} s"


def _time(s: Spread) -> str:
    return f"{_seconds(s.median)} ({_seconds(s.low)}–{_seconds(s.high)})"


def _count(s: Spread) -> str:
    return f"{s.median:g}" if s.low == s.high else f"{s.median:g} ({s.low:g}–{s.high:g})"


def render(rows: Sequence[Row], command: str, env: Mapping[str, str]) -> str:
    """docs/benchmark-speed.md (pure)."""
    runs = ", ".join(f"{r.runs} {r.mode}" for r in rows)
    lines = [
        "# Speed benchmark",
        "",
        f"Measured {env['date']} on {env['platform']}, Python {env['python']}, "
        f"lgcov {env['lgcov']}: {runs} runs of `lgcov ci` on the demo PR (PR #7's prompt "
        "change, which makes `respond` drift), alternating, after one warm-up run that "
        "isn't counted. Median (min–max):",
        "",
        "| mode | wall-clock | agent | judges | live / served nodes | judge calls | tokens |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        tokens = "—" if r.tokens is None else _count(r.tokens)
        lines.append(
            f"| {r.mode} | {_time(r.wall)} | {_time(r.agent)} | {_time(r.judges)} "
            f"| {_count(r.live)} / {_count(r.served)} | {_count(r.judge_calls)} | {tokens} |"
        )
    lines += [
        "",
        "- served: nodes whose code and input match the cassette return the recorded output;"
        " `--live`: every node runs.",
        "- wall-clock is measured around each `lgcov ci` process, so it includes Python"
        " start-up and imports. agent (replaying the runs) and judges (the KPI gate) come from"
        " the run's own timing (`--timing-out`).",
        "- The demo's LLM is a deterministic fake that answers at once, and its judges are a"
        " code check and an offline fake, which report no tokens. So these numbers measure"
        " lgcov itself, not model calls.",
        "",
        "Reproduce:",
        "",
        "```bash",
        command,
        "```",
        "",
    ]
    return "\n".join(lines)


def _sample(wall: float, raw: object) -> Sample:
    if not isinstance(raw, dict):
        raise ValueError("timing.json: not an object")

    def number(key: str) -> float:
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"timing.json: '{key}' is not a number")
        return float(value)

    tokens = raw.get("tokens")
    return Sample(
        wall=wall,
        agent=number("agent"),
        judges=number("judges"),
        live=int(number("live")),
        served=int(number("served")),
        judge_calls=int(number("judge_calls")),
        tokens=tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
    )


def _run(project: Path, extra: Sequence[str]) -> Sample:
    """One fresh `lgcov ci` process on the demo PR; exit 1 is the drift it should find."""
    start = perf_counter()
    done = subprocess.run(
        [sys.executable, "-m", "lgcov.cli", *CI_ARGS, *extra],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    wall = perf_counter() - start
    if done.returncode not in (0, 1):
        raise RuntimeError(f"lgcov ci exited {done.returncode}: {done.stderr[-500:]}")
    return _sample(wall, json.loads((project / "timing.json").read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lgcov.bench")
    ap.add_argument("--runs", type=int, default=5, help="runs per mode")
    ap.add_argument("--demo", default="examples/demo_agent", help="the demo agent's folder")
    ap.add_argument("--out", default="docs/benchmark-speed.md", help="write the table here")
    args = ap.parse_args(argv)
    demo = Path(args.demo)
    source = (demo / "graph.py").read_text(encoding="utf-8")
    if PROMPT[0] not in source:
        print(f"bench: {demo / 'graph.py'} no longer has {PROMPT[0]}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "graph.py").write_text(source.replace(*PROMPT), encoding="utf-8")
        shutil.copytree(demo / "cassettes", project / "cassettes")
        shutil.copy(demo / "taxonomy.yaml", project / "taxonomy.yaml")
        _run(project, MODES[0][1])  # warm-up: disk caches and bytecode, not counted
        samples: dict[str, list[Sample]] = {mode: [] for mode, _ in MODES}
        for _ in range(args.runs):
            for mode, extra in MODES:  # alternating, so no mode gets all the slow runs
                samples[mode].append(_run(project, extra))
        rows = [aggregate(mode, samples[mode]) for mode, _ in MODES]
    env = {
        "date": datetime.now(UTC).date().isoformat(),
        "platform": f"{platform.platform(terse=True)} ({platform.machine()})",
        "python": platform.python_version(),
        "lgcov": version("lgcov"),
    }
    doc = render(rows, "make bench-speed", env)
    Path(args.out).write_text(doc, encoding="utf-8")
    print(doc, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
