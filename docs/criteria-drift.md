# Criteria drift (T196, spec)

When the *meaning* of a pass or fail moves — a rubric edit, a new label form, a taxonomy rename,
or a batch of overrides that redefine the KPI — the judge's band and the gate's α spend are
calibrated against yesterday's criteria. This page is the loop to notice that and repair it.
**Nothing here is automated yet.**

## What "criteria drift" is

Not agent drift (a PR changing a prompt). Criteria drift is:

| Signal | Example |
|---|---|
| Taxonomy / rubric change | Edit `taxonomy.yaml` rubric, labels, or `check:` for a KPI the gate already used |
| Label-set change | New human labels or overrides that disagree with the old majority on the same cases |
| Form change | `question_form` / label vocabulary changes so old rows are not comparable |
| Judge prompt change | `judge.prompt_sha256` moves (gate round locks this) while bands stay old |

A locked **gate round** already refuses a silent plan move (`lgcov guard` / digest). Criteria
drift is the *human* side: the round may still hold while the labels that earned the band no
longer match how maintainers score today.

## Loop (detect → recalibrate → refresh → gate)

```text
1. Detect
   - Diff taxonomy / rubrics / label CSVs against the round that set the band.
   - Watch override rate and disagreement clusters per KPI (review overrides, calibrate report).
   - Gate round: judge prompt hash or KPI set changed → new round required anyway.

2. Recalibrate
   - Freeze a label set under the *new* criteria (blind where possible).
   - `lgcov calibrate` against those labels; do not mix old and new criteria in one band file.

3. Band refresh
   - Write new `calibration/bands.json` on the **base** (PR can't grade itself).
   - Drop or quarantine label rows scored under the old criteria.

4. Gate impact
   - Lock a new gate round (`lgcov round new`) so candidates, margins, and judge hash match.
   - Until then: treat the old band as stale — flag/advisory, not a silent BLOCK on the new meaning.
   - Document in the change record / PR why α and bands moved.
```

## What already exists

| Piece | Where |
|---|---|
| Bands from labels | `lgcov calibrate`, `calibration/bands.json` |
| Override → label | `lgcov review override` ([review.md](review.md)) |
| Judge prompt lock | gate round `judge.prompt_sha256` ([round.md](round.md)) |
| PR can't edit policy | `--policy-dir` / Action evaluate on base ([trust-boundary.md](trust-boundary.md)) |
| Pools vs fit leakage | `lgcov pools check` ([pools.md](pools.md)) |

## What this does not do (yet)

- Auto-detect rubric paraphrase or silent label-policy change.
- Auto-invalidate bands when taxonomy drifts.
- Count cumulative loss from criteria changes (related: [round.md reference pointer](round.md#cumulative-loss-pointer-options-t234)).
- Replace human review of whether two label sets share a meaning.

## Related

- [gate.md](gate.md) — bands and blocking / flag / advisory
- [taxonomy.md](taxonomy.md) — KPI / judge fields
- [judge-commit-first.md](judge-commit-first.md) — commit-first mode must not inherit one-step bands
