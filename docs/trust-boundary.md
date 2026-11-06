# The trust boundary

A PR shouldn't be able to grade itself. This page says what stops that today, and what doesn't
yet.

## What holds

On a `pull_request` run the Action checks out the base commit and `lgcov ci` reads the gate's
policy from there (`--policy-dir`), never from the PR's checkout:

- `lgcov.yaml`, all of it: `gate.margin`, `advisory_kpis`, `blocking_kpis`, `ignore_paths`, the
  coverage floor and `graph_entrypoint`.
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

## What is NOT covered

1. **The PR's code.** This is the largest gap. The verdict is computed in the same process and job
   as the code under test. That code can patch lgcov, append `exit-code=0` to `$GITHUB_OUTPUT`, or
   rewrite the base checkout before it's read. Base-ref loading stops edits in a diff, not a PR
   whose code attacks the gate. Moving the verdict to a job the PR's code never runs in is T179.
2. **A forged result.** The binding catches a result that was edited, is stale or came from other
   policy. It is not a signature, and it can't catch a forger who recomputes every field: every one
   of those values is public, and while the PR's code shares the job it can write the result and
   the report itself. `test_the_known_limit_a_forger_who_recomputes_every_field_passes` pins this.
   Only T179 closes it, by computing the result in a job the PR's code never runs in.
3. **Cassettes.** A PR's edits to `cassette_dir` are still honoured, or `lgcov cassette accept`
   could never pass in its own PR. So a clean re-baseline still exits 0. It can't pass unnoticed: the
   report flags it (`CiResult.cassettes_edited`), and it can't read as a SHIP, because its
   headline is "No decision: needs_human". A blocking code check that passes on the base's
   cassettes and fails on the PR exits 1 all the same. Other KPIs get no such comparison. A run
   the PR removes has nothing to compare against, so the report names it, and it doesn't block.
4. **Secrets in the job.** The README's `JEV_API_KEY` and `LANGSMITH_API_KEY` go in the job that
   runs the PR's code, which can read them. Until T179, only put them there if PR authors are
   trusted: no fork PRs, no outside contributors. A changed node that calls a model needs its key
   in that job too, so "no secrets" also means such a node can't run live.
5. **The job's token.** It holds `pull-requests: write`, and `actions/checkout` leaves it in
   `.git/config` by default, where the PR's code can read it.
6. **The workflow file.** On `pull_request`, GitHub runs the workflow as the PR left it. A same-repo
   author can edit it to drop the gate or to point `uses:` at another ref. This repo's own workflow
   has `uses: ./`, so it also runs the PR's `action.yml`.
7. **Reading labels and expected outputs.** The PR's code can read `calibration/` in its checkout
   and the cassettes replay needs. Anything committed to a public repo is world-readable anyway.
   T179 can take labels out of the job, but replay still has to serve recorded outputs.
8. **Gaming the test.** Code that recognises a recorded input and returns the recorded output
   passes. That is for a reviewer to catch.

## What to set up

No job layout stops an author who can edit the workflow, so protect the gate's own files with a
ruleset or CODEOWNERS on the base branch:

```text
/.github/        @your-team
/lgcov.yaml      @your-team
/taxonomy.yaml   @your-team
/calibration/    @your-team
**/baseline.json @your-team
```

CODEOWNERS only blocks a merge once a branch protection rule or ruleset requires code-owner review.
Use your own taxonomy path if it differs.
