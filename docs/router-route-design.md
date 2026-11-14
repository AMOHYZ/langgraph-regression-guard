# Router root only when the route changed (design, T221)

**Status: design only.** Nothing here is built. This file changes no code, no cassette field
and no label. T175's rule and its documented limit stay as they are until this lands.

## The problem

Since T175 a node whose router changed is root of every run where a node drifts after it
(`explain_runs` in [`attribution.py`](../src/lgcov/attribution.py)). The rule doesn't check
that the run took another branch. So a router edit that routes exactly as before is named
root of drift that a changed node, or model noise, caused.

T221 names the router's source node only when the route differs. Otherwise the existing
labels apply, as if the router had not been edited.

## Why it needs a cassette change

A recorded step holds `node`, `output`, `input`, `code`, `goto` and `router` (the router's
fingerprint). It does not hold:

- the state the router saw. LangGraph hands a router the state after the node's writes, not
  the node's input, so `input` and `output` together don't always rebuild it (reducers,
  other nodes' writes in the same step).
- the targets the router returned.

Both are new fields, so T221 is a cassette schema change. The gate's evaluator runs base's
lgcov, so it needs the read-first, write-later rollout T162 used: one release reads the new
fields, a later one writes them. Baselines must be re-recorded to gain the fields.

**Held until T193.** Subgraph support also changes the cassette schema. The two land as one
schema change, with one re-record, not two.

**A fingerprint-scheme field goes in the same change.** Since T212 lgcov computes two
fingerprints for a node, the current one and the one from before T212, because a cassette
doesn't say which scheme its values use. Once a cassette carries that as a field, the old
scheme (`_Raw` in [`fingerprint.py`](../src/lgcov/fingerprint.py)) can go.

## New fields on a step

| Field | Recorded | Holds |
|---|---|---|
| `routed` | Always, for a node with a conditional edge | The targets its router returned, as node names, and `{"send": node, "arg": payload}` for a Send, as `goto` does |
| `router_state` | By default; size-capped, and can be switched off | The state the router was given |

- `router_state` is masked exactly like a step's input: the same `ignore_paths` and volatile
  fields.
- Over the size cap it is skipped for that step, and rule 2 below applies to it. The cap and
  the switch are settings in `lgcov.yaml`; their names and the default cap are open.

## The rule

For each node whose router fingerprint changed, per visit of that node in a run:

1. **With `router_state`:** run head's router on base's recorded `router_state`. The node is
   named root only when the targets it returns differ from base's recorded `routed`.
2. **Without `router_state`** (over the cap, or switched off): compare `routed` on base with
   `routed` on head, paired by visit number. Immediate targets only: nothing that ran later
   is compared. This is weaker than rule 1, because an upstream change can alter the route
   through an unedited router, so the comment says which rule decided.

In both, order doesn't count for targets that run in parallel, as for `goto`.

If no visit's route differs, the router is not named and the existing labels apply
(root by fingerprint and probe, propagated, inconclusive).

### Cases with their own ruling

| Case | Label |
|---|---|
| The router calls a model | Not named. Existing labels apply. One live sample can't be told from noise, as for a model node |
| The recording predates `routed` | Router not named. The run's first drifted node is inconclusive, and the message says the recording predates route capture and needs a re-record. T175's rule is not kept for old recordings |
| Head's router raises on base's state | Caught at the probe boundary: inconclusive, with the reason. Never a crash |

## Trust boundary

The router probe runs the PR's code. So it runs in the candidate job, like T162's node
probes, and never in the evaluator. The candidate file carries what the router returned; the
evaluator only compares it with base's `routed`. That is a change to the candidate format
too, with the same read-first rollout.

The probe runs with the network blocked, like the node probes. A router that reaches for the
network is the model-router case above.

## Tests to write first

Each fails on today's code, where the changed router is root in all four.

1. **Same targets, plus a node edit.** The router is edited but routes as before, and a
   changed node drifts after it. Root is the changed node only.
2. **Same targets, no changed node.** The router is edited, routes as before, and a node
   drifts. The router is not named; the first drifted node is inconclusive (unexplained).
3. **An upstream edit changes the route.** The router is edited but returns base's targets on
   base's `router_state`; head's run takes another branch because an earlier node changed.
   Rule 1 doesn't name the router. Rule 2 would, which is the limit stated above, and a
   second test pins that.
4. **A loop.** The router is visited several times and routes differently only on the second
   visit. Visits pair by number, and the router is named.

Also one test per row of the rulings table, and one that a `router_state` over the cap falls
back to rule 2.

## What stays true until this is built

T175's limit, as documented: a changed router is root whenever a node drifts after it,
measured on synthetic edits only, and a router edit that routes as before is named root of
drift it didn't cause.
