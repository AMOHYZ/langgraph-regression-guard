# Gate rounds

A gate run locks its two commits for that one run. A **gate round** fixes the plan across runs:
which cases, which judge, which margins, and which commits may use the gate. `lgcov guard` exits 2
if any of that has moved, or if the commit isn't one the round locked. `exposure.json` records what
has already seen each case, and outlives the round.

## Naming: gate round vs benchmark round

**The word "round" means two different things in this repo.** They share the `lgcov round …`
verb prefix and often the filename `lgcov-round.yaml`, but they are not interchangeable:

| | Gate round (this page) | Benchmark round |
|---|---|---|
| Purpose | Lock what `lgcov guard` / the gate may use across PRs | Lock what `lgcov bench` compares |
| Commands | `lgcov round new`, `lgcov guard` | `lgcov round init`, `lock`, `check` |
| Default path | `lgcov-round.yaml` at the **repo root** | Any path you choose (usually under `docs/benchmark/rounds/`) |
| Ledger | `exposure.json` (JSON object) | `exposure.jsonl` next to the round file |
| Docs | this page | [benchmark-rounds.md](benchmark-rounds.md) |

Neither family of commands reads the other's files. Saying "the round" without "gate" or
"benchmark" is ambiguous — prefer the full name. Case pools (optimiser vs gate) and
`lineage.json` are a third concern: [`lgcov pools check`](pools.md).

**`lgcov ci` (evaluate / single-job) now checks the gate round on the base** (`--policy-dir`),
without calling `--record` and without writing `exposure.json`. Mapping (T235):

| Condition | `lgcov ci` exit | `lgcov guard` |
|---|---|---|
| No `lgcov-round.yaml` on the base | skip; exit unchanged | n/a |
| Missing ledger, or round already unlocked on the base (candidate not locked) | **2** (setup) | **2** |
| Digest mismatch, or locked gate material drifted / PR changed round or ledger | **1** | **2** (never 1) |
| Clean | continue | **0** |

The candidate job never runs this check. Standalone `lgcov guard` is exit 0 or 2 for
lock/setup (including `reference.path` re-hash); cumulative-loss breach is exit 1 (T234).

**Proposal mode** is a separate command: [`lgcov propose`](propose.md) (`--ref` or
`--node --prompt`). It never records exposure.

### Cumulative loss vs the kept reference (T234, option 2)

When a later round's `reference` carries a kept `path` copy, cumulative loss is checked
**directly against that reference** — not by summing intermediate rounds
(`lgcov.cumulative_loss.loss_vs_reference`):

| Rule | Behaviour |
|---|---|
| **No summing** | Each candidate is paired-tested against the kept reference with the existing paired test. |
| **Budget** | The KPI's non-inferiority margin `m` vs the reference. |
| **Breach** (loss beyond `m`) | **BLOCK**, **exit 1**. |
| **Inconclusive** vs reference | **needs_human** (exit 0 unless `--fail-on-inconclusive` / `gate.inconclusive_blocks`). |
| **Missing `reference.path` file or hash mismatch** | **exit 2** (`lgcov guard` / `lgcov ci` re-hash). |

This extends the earlier exit-code contract: a cumulative-loss **breach** is exit 1 (lock
checks alone stay 0/2). Digest-only pointers (no `path`) skip the re-hash.

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
| `reference` | The reference round's number and digest, and optionally `path` to a kept copy (T234 option 2). A first round is the reference and has no such key. |
| `digest` | A hash of every field above, so a hand edit exits 2. |

Adding, removing or editing a case's inputs changes `gate_cases.sha256`. Reordering the runs doesn't,
and neither does `lgcov cassette accept`: a re-baseline changes outputs, not cases, and the gate's
own checks on a PR that edits its cassettes still apply.

`lgcov round new` exits 2 if a `jev` KPI has no pinned model, because `jev-latest` can change with no
hash moving.

## `lgcov guard`

It exits 0 when everything matches and 2 on lock/setup problems (including a missing or
mismatched `reference.path`), naming every problem. Cumulative-loss **breach** vs the kept
reference is **exit 1** via `loss_vs_reference` — see above. It compares the round with:

- the case set in the checkout under test, the file `lgcov ci` would read;
- the judge's model, version and prompt hash;
- `alpha`, `power`, each KPI's margin and K_gate;
- the candidate: `--candidate` if given, else the PR's head on a `pull_request` event, else `HEAD`.
  On a PR, `HEAD` is GitHub's merge commit, which no round can lock;
- `exposure_floor`, so a deleted or reset ledger is caught;
- `reference`: a round numbered 2 or more must have one, and it must name an earlier round;
- `reference.path` (when set): the kept copy must exist and re-hash to `reference.digest`
  (missing file or mismatch → exit 2).

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

## The reference round

A later round's cumulative loss is counted from one fixed round, the reference. The pointer:

```yaml
reference:
  round: 1
  digest: 60485ea9991390bab309586bc83c4044f97864448b08c32227889a452a16deb2
  path: rounds/1.yaml   # optional; T234 option 2 — kept copy guard re-hashes
```

The first round is the reference and has no `reference` key. The second round points at the first
round's number and digest, and every later round carries that pointer unchanged. You don't write
`reference` itself: `lgcov round new` refuses a draft that has one. To attach a kept copy, set
person-written `reference_path: rounds/1.yaml` on the draft (or on a locked round before
`lgcov round new`); the lock writes it under `reference.path`. It is inside the digest, so
editing or removing it makes `lgcov guard` exit 2.

A key a round doesn't have is left out of its digest, so a round locked before `path` exists
keeps its bytes and its digest when that key arrives. Digest-only remains the minimum lock;
git-history can still help recovery but is not the sole source of truth.

### Cumulative-loss pointer options (T234)

| Option | What it stores | Status |
|---|---|---|
| **Digest-only** | `reference.round` + `digest` | Minimum lock (always) |
| **Kept `path` copy** | Digest plus a path to a committed snapshot of the reference round | **Implemented** — `lgcov guard` re-hashes; see exit mapping above |
| **Git-history** | Digest + commit/SHA that held the reference round | Not built; recovery aid only |

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
- **Without `reference.path`, nothing re-checks the reference round's bytes.** `lgcov-round.yaml`
  is rewritten each round, so with a digest-only pointer the reference content is only in git
  history. With option 2's kept `path`, `lgcov guard` re-hashes that file. Someone who edits
  `reference` and then runs `lgcov round new` still gets a valid file. On a PR, `--policy-dir`
  reads the base's file, so changing the reference takes a commit to the base branch.
- **`--candidate` is taken as given,** and the digest is not a signature: everything in the file is
  public. A PR's code that shares the job can still forge the result, as
  [the trust boundary](trust-boundary.md) says, so protect both files with CODEOWNERS.
