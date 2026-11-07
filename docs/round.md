# Rounds

A gate run locks its two commits for that one run. A round fixes the plan across runs: which cases,
which judge, which margins, and which commits may use the gate. `lgcov guard` exits 2 if any of that
has moved, or if the commit isn't one the round locked. `exposure.json` records what has already
seen each case, and outlives the round.

**`lgcov ci` and the Action don't call `lgcov guard` yet.** Until they do, a round holds only where
you run the command yourself, and nothing records exposure on its own.

## The round file

You write these fields in `lgcov-round.yaml`:

```yaml
version: 1
gate_cases:
  path: cassettes/baseline.json
kpis:             # optional; a KPI you don't list gets no tags
  - name: respond_runs
    tags: [flow]
delta_1: 0.0      # Δ₁: the difference, PR minus base, the round is sized for
prior_rho: 0.6    # prior ρ: the expected correlation of base and PR outcomes
candidates:       # the commits that may use this gate, as full SHAs
  - 9fceb02d0ae598e95dc970b74767f19372d61af8
stop_rules:       # any names; each a number, a string or true/false
  max_cases: 800
```

`lgcov round new` computes the rest and rewrites the file, which drops your comments:

| Field | What it holds |
|---|---|
| `round` | The previous round's number plus 1. |
| `gate_cases.sha256` | A hash of each run's `inputs` at `gate_cases.path`, in any order. |
| `judge.model` | `jev_model` from `lgcov.yaml`. |
| `judge.version` | lgcov's version. |
| `judge.prompt_sha256` | A hash of each KPI's name, judge kind, node, rubric, labels, hints, scores and check. |
| `kpis[].margin` | The KPI's own `margin` in the taxonomy, else `gate.margin`. |
| `alpha`, `power` | `gate.alpha` and `gate.power`. |
| `k_gate` | The blocking KPIs, as [docs/gate.md](gate.md) counts them. This is not the number of candidates. |
| `exposure_floor` | How many entries `exposure.json` had when this round started. |
| `digest` | A hash of every field above, so a hand edit exits 2. |

Adding, removing or editing a case's inputs changes `gate_cases.sha256`. Reordering the runs doesn't,
and neither does `lgcov cassette accept`: a re-baseline changes outputs, not cases, and the gate's
own checks on a PR that edits its cassettes still apply.

`lgcov round new` exits 2 if a `jev` KPI has no pinned model, because `jev-latest` can change with no
hash moving.

## `lgcov guard`

It exits 0 when everything matches and 2 otherwise, naming every problem. It never exits 1. It
compares the round with:

- the case set in the checkout under test, the file `lgcov ci` would read;
- the judge's model, version and prompt hash;
- `alpha`, `power`, each KPI's margin and K_gate;
- the candidate: `--candidate` if given, else the PR's head on a `pull_request` event, else `HEAD`.
  On a PR, `HEAD` is GitHub's merge commit, which no round can lock;
- `exposure_floor`, so a deleted or reset ledger is caught.

With `--policy-dir`, it reads the round, the ledger, `lgcov.yaml`, the taxonomy and the bands from
the base checkout, with no fallback to the PR's files, as `lgcov ci` does for its policy.

## `exposure.json`

Which judge versions and candidates saw each case, and in which rounds:

```json
{
  "cases": {
    "3f2a9c41d07e5b86": {
      "candidates": ["9fceb02d0ae598e95dc970b74767f19372d61af8"],
      "judges": ["jev-2026-10@0.1.0/41ab92c07de1"],
      "rounds": [1, 3]
    }
  },
  "version": 1
}
```

A case is named by a hash of its inputs, not `run-3`, because a run's number changes when the case
set does. Only `lgcov guard --record` adds to the file, after a clean check. `lgcov round new` never
rewrites it, and a case a later round drops keeps its history.

## A round, start to finish

A commit can't contain its own SHA, so the round goes on the base and names a candidate on a branch.

1. Push the candidate's branch and note its full SHA.
2. On the base, write the fields above, lock the round, and commit both files:

   ```bash
   lgcov round new
   ```

   ```bash
   git add lgcov-round.yaml exposure.json
   ```

3. Check the candidate's checkout before gating it, with the base checked out at `base/`:

   ```bash
   lgcov guard --policy-dir base
   ```

4. On the base, record what saw the cases and commit `exposure.json`. `--record` refuses
   `--policy-dir`, because that checkout is thrown away and the record would be lost.

   ```bash
   lgcov guard --candidate <full-sha> --record
   ```

5. For the next round, edit what you write and run `lgcov round new` again. Any new push to a
   candidate needs this, since its SHA has changed.

## What this doesn't do

- **Tags, Δ₁, prior ρ and stop rules are locked but unused.** The gate doesn't read them, and nothing
  applies a stop rule.
- **There is no correction across candidates.** Holm covers the KPIs in one run, and each locked
  candidate gets the full α. With 4 candidates on one case set, the chance that at least one wrongly
  SHIPs can reach 10% (4 × 2.5%).
- **`judge.version` only moves with lgcov's version,** so a change to lgcov's judges between releases
  doesn't move it. `judge.model` is `jev_model`; a `judge: llm` KPI has no model setting to lock.
- **`exposure_floor` covers the ledger as it stood when the round started.** Only git protects what
  was recorded since. It is a count, so it catches accidents, not someone replacing entries.
- **Uncommitted changes aren't covered.** `lgcov guard` warns about them, as `lgcov ci` does.
- **`--candidate` is taken as given,** and the digest is not a signature: everything in the file is
  public. A PR's code that shares the job can still forge the result, as
  [the trust boundary](trust-boundary.md) says, so protect both files with CODEOWNERS.
