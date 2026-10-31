"""T078: the Action's PR comment, built from one `lgcov ci` run (pure)."""

from __future__ import annotations

from dataclasses import dataclass

from lgcov.drift_gate import FailOn, verdict

MARKER = "<!-- lgcov-report -->"  # T081: the comment upsert finds its own comment by this
HARNESS_BANNER = "⚠️ Harness error, not a regression"
_TITLES = {
    "pass": "🟢 lgcov: pass",
    "fail": "🔴 lgcov: drift",
    "error": "⚠️ lgcov: harness error, not a regression",
}


@dataclass(frozen=True)
class CiResult:
    fail_on: FailOn
    exit_code: int
    graph_lines: tuple[str, ...] = ()
    graph_note: str = ""
    replay_lines: tuple[str, ...] = ()
    root_drift: tuple[str, ...] = ()
    propagated_drift: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    harness_error: str = ""
    coverage_line: str = ""  # T083, e.g. "Nodes 75% (−25% vs main) · Edges 60% (±0)"
    newly_uncovered: tuple[str, ...] = ()


def render_ci_report(r: CiResult) -> str:
    v = verdict(r.exit_code)
    lines = [MARKER, f"## {_TITLES[v]}", ""]
    if r.harness_error:
        # Keep a stray ``` in the error from closing the fence early.
        error = r.harness_error.replace("```", "'''")
        lines += [
            f"{HARNESS_BANNER}: lgcov could not finish, so this PR was not checked.",
            "",
            "```",
            error,
            "```",
            "",
        ]
    if r.graph_lines or r.graph_note:
        lines += ["### Graph changes", ""]
        lines += [r.graph_note, ""] if r.graph_note else []
        lines += [*r.graph_lines, ""]
    if r.replay_lines:
        lines += [*r.replay_lines, ""]
    if r.coverage_line:
        lines += [f"Coverage: {r.coverage_line}", ""]
    if r.newly_uncovered:
        lines += ["Newly uncovered: " + ", ".join(f"`{n}`" for n in r.newly_uncovered), ""]
    lines += [*r.notes, ""] if r.notes else []
    lines.append(
        f"fail-on: `{r.fail_on}` · root drift: {len(r.root_drift)} · "
        f"propagated drift: {len(r.propagated_drift)} · verdict: **{v}**"
    )
    return "\n".join(lines) + "\n"
