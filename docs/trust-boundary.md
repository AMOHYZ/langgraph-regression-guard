# The trust boundary

A PR shouldn't be able to grade itself. This page says what stops that today, and what doesn't
yet.

## What holds

On a `pull_request` run the Action checks out the base commit and `lgcov ci` reads the gate's
policy from there (`--policy-dir`), never from the PR's checkout:

- `lgcov.yaml`, all of it: `gate.margin`, `advisory_kpis`, `blocking_kpis`, `ignore_paths`,
  `mask_volatile_in_key`, the coverage floor and `graph_entrypoint`.
- The taxonomy, including a KPI's own `margin`.
- `calibration/bands.json` and `calibration/cases/`.

A PR's edits to these change nothing until it merges, and the report names them. If the base
commit can't be checked out, the run gives no verdict and exits 2. It never falls back to the PR's
files.

Labels only feed the report's calibration lines, and never the exit code.

`tests/test_workflow_lint.py` fails CI if a workflow here uses `pull_request_target`, or if a job
that runs the PR's code can read a secret. Fork PRs get a read-only token and no secrets, so their
report goes to the job summary.

Each run writes a result bound to the base SHA, the head SHA, the hashes of the policy files and
the hash of its report. `lgcov verify-result` runs between the run and the PR comment, from the
Action rather than the project's venv, and compares those with the event and a fresh hash of the
base checkout. On any mismatch, or if it can't run, the report is replaced with a harness error, no
verdict is posted and the run exits 2.

Two cases work differently:

- **The first adoption PR.** The base has no `lgcov.yaml` and doesn't already gate through the
  Action's inputs, so there is no policy to protect. The run uses the PR's own, and the report's
  headline says it is advisory and that enforcement starts after merge. It exits 0, sets
  `verdict=advisory` and never claims a SHIP. If nothing could run, such as a graph that won't
  import, it still exits 2.
- **A PR that moves the graph.** `graph_entrypoint` comes from the base, so the run exits 2 and
  says so. Land the new path first, or keep a shim at the old one.

## One job or two

The Action's `phase` input sets where the PR's code runs. The default, `all`, does everything in one
job. With two jobs, the README's [layout](../README.md#two-jobs-so-the-prs-code-cant-touch-the-verdict),
that code never runs where the verdict is decided:

| | `phase: candidate` | `phase: evaluate` |
|---|---|---|
| **Checks out** | The PR's merge commit, with `persist-credentials: false` | The base commit, and the PR's commit as data, with symlinks off |
| **Installs** | Your project and lgcov | lgcov alone, pinned by the Action's own `uv.lock`, with no uv cache and no uv config from either commit |
| **Runs** | The graph and the replay | The comparison, the judges, the report and the comment |
| **Holds** | `contents: read`, and your agent's own model key if you add it | The judge keys, `LANGSMITH_API_KEY` and `pull-requests: write` |

The candidate job hands over one file, `candidate.json`, as an artifact: each run's steps, how each
call ran, and what each node whose code changed returned when run again, offline, on the input the
cassette recorded for it (T162's probes, or why it couldn't run). The job that decides recomputes drift from those steps, with the PR's cassette from git and
the policy from the base. The file has no field for drift, a verdict or an exit code. A missing file,
or one that isn't exactly this shape, gives no verdict and exit 2.

So unless the workflow file itself is edited (limit 5), the PR's code can't:

- read the judge keys, the LangSmith key or the write token;
- patch lgcov, or change the base checkout, in the job that decides;
- write that job's `$GITHUB_OUTPUT`, its result or its report.

`tests/test_action_phases.py` runs both jobs from `action.yml`, with PR code that attacks its own job.
`tests/test_workflow_lint.py` fails CI if this repo's candidate job can hold a write token or keeps its
token in `.git/config`, or if its job that decides can be skipped.

## What is NOT covered

1. **What the PR's code says it did.** The steps and probes in `candidate.json` are that code's word.
   A probe that matches no recorded step gives no verdict, but a forged probe output can make a
   changed node read as propagated, just as forged steps can hide drift. It can
   write a file saying nothing changed, and the job that decides can't re-run the code to check, so
   that passes. `test_the_known_limit_a_file_reporting_the_recorded_outputs_passes` pins this. It
   gets no further than limit 6 does, but it is easier. So the gate catches regressions in code that
   doesn't attack it. It doesn't stop an author who does: that is for a reviewer. Text from the file,
   like a node's name or its error, also reaches the comment, as it does with one job. The headline
   and the exit code are worked out in the job that decides.
2. **One job.** With `phase: all`, the verdict is computed in the same process and job as the code
   under test. That code can patch lgcov, append `exit-code=0` to `$GITHUB_OUTPUT`, or rewrite the
   base checkout before it's read. Base-ref loading stops edits in a diff, not a PR whose code attacks
   the gate. It can also forge the result: the binding catches a result that was edited, is stale or
   came from other policy, but it is not a signature, and every value in it is public.
   `test_the_known_limit_a_forger_who_recomputes_every_field_passes` pins that. Any key in that job
   can be read too, and `actions/checkout` leaves its token in `.git/config` by default. Only put keys
   there if PR authors are trusted: no fork PRs, no outside contributors.
3. **Cassettes.** A PR's edits to `cassette_dir` are still honoured, or `lgcov cassette accept`
   could never pass in its own PR. So a clean re-baseline still exits 0. It can't pass unnoticed: the
   report flags it (`CiResult.cassettes_edited`), and it can't read as a SHIP, because its
   headline is "No decision: needs_human". A blocking code check that passes on the base's
   cassettes and fails on the PR exits 1 all the same. Other KPIs get no such comparison. A run
   the PR removes has nothing to compare against, so the report names it, and it doesn't block.
4. **A model key in the candidate job.** A changed node that calls a model needs its key where it
   runs, and the PR's code can read it there. That's your call, and the default layout has none: only
   add it if PR authors are trusted, and add that key alone. Without it the node raises, which reads as
   an agent failure and exits 1, as on a fork PR today.
5. **The workflow file.** On `pull_request`, GitHub runs the workflow as the PR left it. A same-repo
   author can edit it to drop the gate, to point `uses:` at another ref, or to run the PR's code in the
   job holding the keys, and that run happens before any review. This repo's own workflow has
   `uses: ./`, so its candidate job runs the PR's `action.yml`, and its job that decides runs the
   base's. An evaluator triggered by `workflow_run` runs from the default branch, so it would close
   this for that job; it isn't built.
6. **Gaming the test.** Code that recognises a recorded input and returns the recorded output
   passes, and a model judge reads the PR's outputs, so they can try to talk it round. That is for a
   reviewer to catch.
7. **Reading labels and expected outputs.** The PR's code can read `calibration/` in its checkout
   and the cassettes replay needs. Anything committed to a public repo is world-readable anyway.
   With two jobs lgcov reads the labels in the job that decides, but the candidate job's checkout
   still has them.
8. **What the candidate job uploads.** The artifact holds each run's live outputs for a day, and
   anyone who can read the repo's Actions can download it, which on a public repo is anyone signed in.
   Treat those outputs as you do a committed cassette.
9. **A first adoption PR with two jobs.** There is no policy on the base, so the job that decides
   reads the PR's, and it holds the keys. That PR can spend up to `--max-calls` judge calls on rubrics
   it wrote. It can't read the keys, and it blocks nothing.
10. **Symlinks in the PR.** They stay small plain files in the job that decides, so a `cassette_dir`
    that is a symlink gives no verdict there.

## What to set up

With two jobs, require the check of the job that decides. The candidate job passes whatever it
finds. Give it a `timeout-minutes` too, since the PR's code can hang it.

No job layout stops an author who can edit the workflow, so protect the gate's own files with a
ruleset or CODEOWNERS on the base branch:

```text
/.github/        @your-team
/lgcov.yaml      @your-team
/taxonomy.yaml   @your-team
/calibration/    @your-team
**/baseline.json @your-team
/lgcov-round.yaml @your-team
/exposure.json   @your-team
```

CODEOWNERS only blocks a merge once a branch protection rule or ruleset requires code-owner review.
Use your own taxonomy path if it differs.
