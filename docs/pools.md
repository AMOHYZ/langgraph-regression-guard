# Case pools

Optimiser cases, gate cases and calibration labels stay apart. `lgcov pools check` refuses
overlaps that would let a case used to fit today's judge also sit in the gate pool, or that
would put the same case (or near-exact wording) in two pools.

**Calibration labels stay in `calibration/labels/`.** Pool folders are configurable
(`cases/optimiser` and `cases/gate` by default). There is no parallel `labels/calibration/`
tree.

## Folders

| Path | Role |
|---|---|
| `cases/optimiser/` | Cases writers and tuners may see. |
| `cases/gate/` | Cases used only for gating. |
| `calibration/labels/` | Human / judge label rows for banding (unchanged). |

Each case file is a JSON object with `id` and `text` (the input text only). Optional
T123 fields: `control: true` marks an immutable negative control and requires a non-empty
`immutability_rationale`. Controls are excluded from pass-rate denominators. A control
that passes is `needs_human` with reason `control_passed` (v0; BLOCK comes later).
`lgcov pools check` refuses a control with no rationale, or with a recorded human-labelled
pass (hard-but-solvable is not allowed in v0).

## `lineage.json`

Pool membership and which judge versions were **fitted** on each case:

```json
{
  "version": 1,
  "cases": {
    "order-where": {
      "pool": "gate",
      "judges_fitted": ["jev-1@0.1.0/cccccccccccc"]
    }
  }
}
```

`candidates_seen` is **not** stored here. Read it from [`exposure.json`](round.md)
(`CaseExposure.candidates`). Copying it into lineage is refused.

Elapsed rounds do **not** make a case independent. A later round number, or waiting for more
rounds, does not clear `judges_fitted`.

## `lgcov pools check`

Exits 0 when the pools are clean, and 2 otherwise, naming every problem:

1. **Same case id in two pools** — a case file under both `cases/optimiser` and `cases/gate`.
2. **Gate case the current judge was fitted on** — the current judge is the locked gate
   round's judge (`model`, `version`, prompt hash) on the **base** ref. With no locked round,
   the check cannot decide: that is a refusal, not a pass.
3. **Near-exact duplicate text** — two different ids share a `v1:` hash of normalised input
   text (NFKC, casefold, collapse whitespace, trim). That catches exact and near-exact
   duplicates only, **not** paraphrases.

With `--policy-dir`, lineage and the round file are read from the base checkout.

## In the gate

`lgcov ci` reads lineage and pool folders from the base when `--policy-dir` is set. If the PR
edits `lineage.json` or a pool folder, the verdict is `needs_human` (state `pools_edited`):
not a pass.

## What this doesn't do

- Importers that fill the pools (T147).
- An audit pool or sampler (T138).
- Auto-writing lineage from `lgcov calibrate` or `lgcov guard --record`.
- Any change to gate statistics.
