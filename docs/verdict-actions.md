# What each gate outcome asks of the reviewer

Every `lgcov ci` run ends in exactly one state below. The state sets the PR comment's
headline, the reviewer's next step and the exit code, all from one pure function,
`decide_action` in `src/lgcov/verdict_actions.py`. A test reads this table and checks it
against that function.

## Inputs

| Input | Where it comes from |
|---|---|
| Statistical verdict | The paired KPI gate across the blocking KPIs and their node or slice groups: SHIP, BLOCK or INCONCLUSIVE ([gate.md](gate.md)). None when no blocking KPI was judged. |
| Policy violation | Drift that `fail-on` cares about, node coverage under `thresholds.min_node_coverage`, or a blocking code check that passed on the base and fails on this PR, including one a re-baseline in this PR hides. |
| Judge abstention | A blocking KPI whose judge didn't commit: a BLOCK at low confidence, a BLOCK from a flag-band judge, or more than half its judgments not running. |
| Infra failure | Install, graph load or cassette trouble, network trouble in a node or judge, a blocking KPI with no valid judgment on a side, no llm client for a blocking KPI, a commit moving during the run, or a candidate job that left no result this job can use. |
| Human approval | The `approved-by` input, which your workflow fills in. |
| CI status | The `ci-status` input, which your workflow fills in. lgcov doesn't read your other jobs. |
| Adoption permission | The `mode` input: `enforcing` (the default) or `advisory`. A PR whose base has no policy yet is advisory whatever `mode` says ([trust-boundary.md](trust-boundary.md)). |
| Edited cassettes | This PR changes anything under `cassette_dir`, the expected outputs it is graded on. |

## States

The first matching row wins. "The switch" is `fail-on-inconclusive` or
`gate.inconclusive_blocks`.

| # | State | When | Headline | Next step | Exit, enforcing | Exit, advisory | `verdict` |
|---|---|---|---|---|---|---|---|
| 1 | `infra` | An infra failure and no finding | ⚠️ lgcov: harness error, not a regression | Fix the error below and re-run. This PR was not checked: the agent neither passed nor failed. | 2 | 2 | `error` |
| 2 | `first_adoption` | The base has no policy yet, so the run uses this PR's own | ⚠️ Advisory: policy comes from this PR and is not trusted yet; enforcement starts after merge | Review this PR's lgcov setup like any other config change. The results below are what lgcov would have said; they block nothing. | 0, or 2 with an infra failure | 0, or 2 with an infra failure | `advisory` |
| 3 | `agent_failure` | A node raised | 🔴 lgcov: agent failure | Fix the node named below. The run stopped there, so nothing after it was checked. | 1 | 0 | `fail` |
| 4 | `policy_hidden_code_check` | This PR edited its cassettes, and a blocking code check passes on the base's and fails on this PR | 🔴 code check failed (hidden by a re-baseline in this PR) | `<kpi>` passed on the base's cassettes and fails here on F of N cases (`<cases>`). Fix the regression. If the new behaviour is right, change the check on the base branch first: this PR can't change what grades it. | 1 | 0 | `fail` |
| 5 | `policy_code_check` | A blocking code check passed on the base and fails on this PR, whatever the statistical verdict | 🔴 lgcov: code check failed | `<kpi>` passed on the base and fails here on F of N cases (`<cases>`). Fix the regression. If a check can legitimately vary between runs, make it advisory in `gate.advisory_kpis` on the base branch first: this PR can't change what grades it. | 1 | 0 | `fail` |
| 6 | `policy_drift` | Drift that `fail-on` cares about, whatever the statistical verdict | 🔴 lgcov: drift | Open the root node. If the change is intended, re-baseline with `lgcov cassette accept --nodes <root>`; if not, fix it. | 1 | 0 | `fail` |
| 7 | `block` | Statistical BLOCK | 🔴 lgcov: KPI regression | Fix the root node in the KPI table. If the judge is wrong, record it with `lgcov review override`. | 1 | 0 | `fail` |
| 8 | `policy_floor` | Node coverage under the floor, even with a SHIP | 🔴 lgcov: coverage under the floor | Add cases that reach the uncovered nodes, or lower the floor. | 1 | 0 | `fail` |
| 9 | `not_checked` | Replay stopped early: a budget hit or `on-divergence: fail` | ⚠️ No decision: needs_human | Re-run with a bigger budget or `on-divergence: flag`, or review `<nodes>` yourself: lgcov did not check them. | 0, or 1 with the switch | 0 | `needs_human` |
| 10 | `abstained` | A judge abstained on a blocking KPI, even under a SHIP | ⚠️ No decision: needs_human | Read the cases for `<kpi>` and decide. If the judge is wrong, record it with `lgcov review override`. | 0, or 1 with the switch | 0 | `needs_human` |
| 11 | `inconclusive` | Statistical INCONCLUSIVE | ⚠️ No decision: needs_human | Read the KPI table, then merge on your own judgement or add the cases it asks for. | 0, or 1 with the switch | 0 | `needs_human` |
| 12 | `cassettes_edited` | This PR edits its own cassettes, even under a SHIP | ⚠️ No decision: needs_human | Read the cassette diff and decide: this PR changed the expected outputs it is graded on, so lgcov can't vouch for it. | 0 | 0 | `needs_human` |
| 13 | `drift_not_enforced` | Drift, `fail-on: never`, no statistical verdict | ⚠️ No decision: needs_human | Read the drift table and decide. `fail-on: never` means lgcov won't fail this check on drift. | 0 | 0 | `needs_human` |
| 14 | `advisory_drop_only` | No statistical verdict, and an advisory KPI dropped | ⚠️ No decision: needs_human | `<kpi>` dropped, but no KPI here can block yet. Decide yourself; `lgcov calibrate` is what lets its judge block. | 0 | 0 | `needs_human` |
| 15 | `ship_advisory_drop` | SHIP, and an advisory KPI dropped | ⚠️ lgcov: SHIP (N blocking KPIs), advisory drop in `<kpi>` | Look at `<kpi>`: it dropped, but its judge is advisory and can't block this PR. | 0 | 0 | `pass` |
| 16 | `nothing_ran` | Every node was served, so nothing re-ran; SHIP or no statistical verdict | 🟢 lgcov: every node served, nothing re-ran | Review as usual. lgcov measured nothing here: every node's code and masked input match the recording, so it was served. A change that reaches only a masked value, such as a run id or a message's usage, or a Store isn't seen. | 0 | 0 | `pass` |
| 17 | `ship` | SHIP | 🟢 lgcov: SHIP (N blocking KPIs) | Review as usual. SHIP covers these KPIs on the recorded cases, nothing else. | 0 | 0 | `pass` |
| 18 | `no_drift_no_kpi` | No drift, and no blocking KPI judged | 🟢 lgcov: no drift (no blocking KPI judged) | Review as usual. lgcov compared the recorded runs; it judged no KPI that can block. | 0 | 0 | `pass` |

A run this PR removes from its cassettes gets a note naming it, and changes no row. Rows 4 and 5
block on one case, with no false-alarm bound; [gate.md](gate.md) says why and how to
opt out. Row 16 names what serving can miss, and [the replay audit](validation/replay-audit.md) has
the detail.

## What changes a row, and what doesn't

Nothing below changes the statistical verdict text. Every comment shows it on a status
line under the next step, or says no blocking KPI was judged.

- **An infra failure with a finding (rows 3 to 8)** keeps the finding's headline, and the
  next step adds fixing the harness. The banner says the finding stands and that anything
  after it was not checked. It exits 1, or 2 in advisory mode: a finding wins over an infra
  failure, and advisory mode leaves only the infra failure to report.
- **`mode: advisory`** turns every exit 1 into 0, and an infra failure still exits 2. Red
  headlines gain "(advisory, not blocking)", and the comment says what the mode authorises:
  lgcov reports what it found, and it neither blocks this merge nor approves this PR.
  `verdict` stays what it would be, so a would-be block reads `fail` with exit 0.
- **`approved-by`** adds "Human approval" to the status line. It changes no headline, exit
  code or `verdict`. On rows 9 to 14 the next step becomes the approver's call.
- **A first adoption PR (row 2)** claims no SHIP: the status line and the KPI rows say "would
  SHIP", and the guarantee paragraph is left out.
- **`ci-status`** adds "Other CI" to the status line. `failure` or `cancelled` puts "Fix the
  failing CI first." ahead of the next step. It changes no headline, exit code or `verdict`.

## The green tick

A composite Action can only pass or fail its check, so rows 9 to 14 exit 0 and GitHub shows
a green tick by default. That tick is not a pass. The comment says so in its next step, and
the Action adds a `::warning` annotation whenever `verdict` is `needs_human`. To make rows 9
to 11 fail the check instead, turn the switch on. Row 12 stays exit 0 either way, or a
re-baseline could never land in its own PR: protect `cassette_dir` with CODEOWNERS if that
needs a named reviewer.

## Passing approval and CI status

lgcov doesn't call GitHub for these. Your workflow passes what it knows:

```yaml
on:
  pull_request:
  pull_request_review:
    types: [submitted]

jobs:
  tests:
    # your test job
  lgcov:
    needs: tests
    if: ${{ !cancelled() }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: AMOHYZ/langgraph-regression-guard@v0.2.0
        with:
          mode: advisory
          approved-by: ${{ github.event.review.state == 'approved' && github.event.review.user.login || '' }}
          ci-status: ${{ needs.tests.result }}
```

The comment only changes when lgcov runs, so an approval shows once the review event has
re-run it, and that re-run repeats any model judge calls. The next push runs without a
review event, so the approval line goes away: it was given to the older commit.
