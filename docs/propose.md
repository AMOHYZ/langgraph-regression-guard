# `lgcov propose` (T120 v0)

Gate a proposal without opening a GitHub PR and without recording exposure.
`lgcov guard` is unchanged: lock/check only, exit 0 or 2.

```bash
lgcov propose --ref <full-sha>
lgcov propose --node respond --prompt prompt.txt
```

Pass exactly one of `--ref` or `--node` + `--prompt`. `--policy-dir` is the same as
`lgcov guard` / `lgcov ci`: the base checkout's round, exposure, `lgcov.yaml`, taxonomy
and bands.

## Exit codes

| Code | When |
|---|---|
| 0 | `eligible`, or `needs_human` / `unsupported` by default |
| 1 | `blocked`; with `--fail-on-inconclusive`, also `needs_human` |
| 2 | infra: round moved, unlocked `--ref`, pool/round harness |

JSON goes to stdout. Schema: [guard-schema.json](guard-schema.json).

`exposure_recorded` is always `false` in v0. Stderr prints one line telling you to run
`lgcov guard --record`. Propose never calls `--record`.

## State and `reasons[]`

`reasons[]` names the rule that set `state`:

- root drift under fail-on (the demo: **root drift on `respond`**)
- KPI BLOCK
- pool violation
- candidate not locked
- inconclusive KPI
- node has no declared prompt

INCONCLUSIVE is always `needs_human`, never `eligible`. Overlay (`--node --prompt`) is
never `eligible`: it is capped at `needs_human` with reason **candidate not locked**,
even when the equivalent `--ref` would be `eligible` or `blocked`. Per-node drift labels
still match.

A gate with no ledger trace is visible: `exposure_recorded` is false and stderr names
`lgcov guard --record`.

## Prompt overlay

How `--node --prompt` finds a prompt (it does **not** scrape random strings from the
function body):

1. Prefer a **declared** prompt on that node from recording `parts` / live graph parts
   (T239 chat-model fields). A part name that is clearly a prompt (`*.prompt`,
   `*.system_prompt`) counts.
2. Else look at the node's source only if the graph/node **declares** a prompt attribute
   (`system_prompt` or `prompt` on the node function, or a well-known part name already
   in the cassette parts table).
3. If neither exists: state `unsupported`, reason `node has no declared prompt`.

The overlay rewrites that declared value in a temp copy of the graph file and replays.
`--ref` loads the graph from that commit (`git archive`) and replays against the current
checkout's GATE cassettes (`lgcov-round.yaml` `gate_cases` / `cases/gate`). Optimiser-pool
cases are never judged.

`--ref` that is not a locked candidate exits 2, the same as `lgcov guard`. Overlay does
not write the T119 lock file.

## `--optimiser-proposal`

Sets **K_gate=1** for Holm / N\* sizing and prints the **full audit list** (every KPI
row, including advisory). It does not read optimiser-pool cases and does not set a PR
label (T235 is out of scope). FakeJudge and uncalibrated model judges never BLOCK.

## Demo

The one-line `SYSTEM_PROMPT` change already in `src/lgcov/bench.py` (`PROMPT`) and
`examples/demo_agent/graph.py`. A local commit of that tweak, passed as `--ref`, is
blocked for **root drift on `respond`**, not a KPI BLOCK. Do not open a GitHub PR for
the demo.
