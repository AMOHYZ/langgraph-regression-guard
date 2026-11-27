# Attribution benchmark

**5-10 PRs are a pilot, not a precision guarantee, and so far there's one PR.** On open_deep_research #163, run twice, root recall is 0.17 (1 of 6) on the pilot and 0.20 (1 of 5) on the `gpt-6-luna` rerun, so lgcov missed most of the labelled roots. Root precision is 1 of 1 (95% interval 0.21 to 1.00), and it's the same single root both times. Re-scored offline with the multi-root rule (T162), the pilot's saved runs name no root: see [Multi-root re-score](#multi-root-re-score-t162). On 72 synthetic cases from 3 questions (2 recorded, 1 in-process), the labels `lgcov ci` ships catch 31 of 31 regressed cases and name a root on 24 of 24 harmless ones (T262); both node-naming baselines flag all 24. Counted per node, root precision is 0.56 (31 of 55). Under `fail-on: harmed-root` with the suite's code-check taxonomies, enforced-root false flags are 0.25 (6 of 24) and measured recall is 0.83 (10 of 12) (rooted cases unmeasured: 0.53 (29 of 55)). By taxonomy tag: requirement enforced-root false flags 0.17 (4 of 24), measured recall 0.82 (9 of 11); format enforced-root false flags 0.08 (2 of 24), measured recall 1.00 (4 of 4).

## What was measured

Whether lgcov names the node a change broke (root) and the nodes that only inherited the damage (propagated), scored against hand-written labels. On the real PR, agent KPIs were also read from the saved runs, kept separate from those scores. Not measured: a judge on a real PR, and how much a live model's answers vary with no change at all.

Each kind of evidence has its own section, and their numbers never combine: demo fixtures, synthetic cases and real PRs. `lgcov bench --synthetic` replaces only its own section. `lgcov bench` overwrites this whole file by default and writes only the demo fixtures table and its known gaps, so pass `--out` with another path and copy those in.

## Limitations

- **One real PR (n=1).** Both runs are the same PR and question, so they aren't two data points.
- **The edited-node baseline ties lgcov on single-node edits,** on recall, 1.00 (20 of 20) each, and on coverage, 1.00 (44 of 44) each. lgcov is ahead only on case-level precision, 0.45 against 0.35: see [Synthetic](#synthetic).
- **`lgcov ci` tells an added node from a newly reached one when the baseline holds `graph` and isn't stale (T243 / T238).** The synthetic bench gives lgcov both graphs, so a node a route stopped or started reaching is propagated there, and exact match on all edits is 1.00 (31 of 31): see [Per node](#per-node). Without a trusted `graph`, a node only the PR reached still reads inconclusive either way and blocks either way. `lgcov bench` gives real rows both graphs too: each side saves the node names of the graph it ran (T242), and `--from-runs` reads them. Runs kept before that have none, so there a node only one side ran stays inconclusive.
- **Drift roots still flag every harmless edit (T262); harmed-root does not.** lgcov names a root on 24 of 24 harmless synthetic cases, and both node-naming baselines flag 24 of 24 too. With the suite's code-check taxonomies, enforced-root false flags under `fail-on: harmed-root` are 0.25 (6 of 24), and measured recall is 0.83 (10 of 12) (rooted cases unmeasured: 0.53 (29 of 55)). By taxonomy tag: requirement 0.17 FF / 0.82 recall; format 0.08 FF / 1.00 recall. On edits the diff can't pin to one node, it names a root on 14 of 14 harmless cases.
- **The synthetic bench labels the nodes inside a subgraph, as `lgcov ci` does on a schema-2 baseline** (T193). `lgcov record` writes those units by default. A baseline recorded earlier, schema 1, still calls a subgraph inconclusive, since a subgraph has no fingerprint. Before the bench recorded units it missed 6 of 32 drift roots on edits the diff can't pin to one node, all six inside a subgraph, and on single-node edits the subgraph edits were inconclusive. Now it finds all 32: each of the 32 nodes it names is a drift root, and 14 of them are regression roots. See [Edits the diff can't pin to one node](#edits-the-diff-cant-pin-to-one-node).
- **Synthetic cases come from our own generator,** so they share its assumptions about what an edit looks like; real PRs (T171) are the check.
- **Usage counts are pending.** Request, token and error counts for the `gpt-6-luna` runs and the pilot still have to come from the usage page: see [Cost per run](#cost-per-run).
- **lgcov's root label needs recorded samples to separate model noise.** Base run against itself gave the same root and propagated labels as both #163 runs, so #163's 1 of 1 can't be told apart from model noise without `samples`, and real-PR root precision doesn't test the kill criterion yet: see [A/A noise floor](#aa-noise-floor). The gate reads base's recorded `samples` when present (T229). `lgcov bench --noise-repeats 3` gave no false alarm on one later base-against-base run ([A/A at K=3](#aa-at-k3)).
- **More real PRs (T171) are pending,** so there are no numbers for them.

## Demo fixtures

Run 2026-11-11: does lgcov name the node a change broke (root) and the nodes that only inherited the damage (propagated)? Each benchmark records its cases on base, replays head, and compares lgcov's labels with hand-written ones: the 4 benchmark files in `tests/fixtures/bench`. Precision / recall / F1:

| benchmark | root | propagated | found root | expected root | time | |
|---|---|---|---|---|---|---|
| linear | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 | `b` | `b` | 4.54 s | ✓ |
| multi_root | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 | `b`, `c` | `b`, `c` | 4.34 s | ✓ |
| send_fanout | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 | `worker_b` | `worker_b` | 4.78 s | ✓ |
| subgraph | 0.00 / 0.00 / 0.00 | 1.00 / 1.00 / 1.00 | — | `sub.inner` | 5.13 s | ❌ known gap (subgraph) |
| **micro** | 1.00 / 0.80 / 0.89 | 1.00 / 1.00 / 1.00 | | | | |
| **macro** | 0.75 / 0.75 / 0.75 | 1.00 / 1.00 / 1.00 | | | | |

### Known gaps

- `subgraph` (subgraph): lgcov said root —, expected `sub.inner`. Inconclusive: `sub` (no fingerprint: a subgraph or the like (T193)). The PR edits inner, inside the subgraph node sub; after only drifts because of it.

Reproduce:

```bash
lgcov bench tests/fixtures/bench
```

## Synthetic

**24 of 24 harmless refactors are named root.** Under `fail-on: harmed-root`, enforced-root false flags are 0.25 (6 of 24); measured recall is 0.83 (10 of 12) (unmeasured rooted cases 0.53 (29 of 55)). By taxonomy tag: requirement enforced-root false flags 0.17 (4 of 24), measured recall 0.82 (9 of 11) (unmeasured 0.62 (34 of 55)); format enforced-root false flags 0.08 (2 of 24), measured recall 1.00 (4 of 4) (unmeasured 0.78 (43 of 55)).

Run 2026-11-22: 72 cases from 3 questions (2 recorded, 1 in-process) across 24 distinct edits on 3 offline agents, covering all 8 classes in [mutations.yaml](mutations.yaml) (31 regressed, 24 benign, 17 inert). Suite recordings are schema 2 (default since #208). No paid calls. These numbers never combine with the real PRs below.

- **Precision:** of the cases where a method names a node, the share where that node is the edited one and the edit regressed.
- **Recall:** of regressed cases, the share where the edited node is named.
- **Coverage:** of cases where the edit changed the run, the share where the edited node is named.
- **False flag:** of cases where the edit changed the run and nothing regressed, the share where a method names a node.

Single-node leaves out the router and fan-out edits, which change a conditional edge. All scores them against the edge's source node, the node that routes: its own output doesn't change, so a method that names it has to see the router's code.

### By case

| Edits | Method | Precision | Recall | Coverage | False flag |
|---|---|---|---|---|---|
| All | lgcov root | 0.56 (31 of 55) | 1.00 (31 of 31) | 1.00 (55 of 55) | 1.00 (24 of 24) |
| All | first drift + router rule | 0.51 (28 of 55) | 0.90 (28 of 31) | 0.89 (49 of 55) | 1.00 (24 of 24) |
| All | edited node from the diff | 0.43 (31 of 72) | 1.00 (31 of 31) | 1.00 (55 of 55) | 1.00 (24 of 24) |
| All | first divergence | 0.31 (17 of 55) | 0.55 (17 of 31) | 0.69 (38 of 55) | 1.00 (24 of 24) |
| All | end-to-end | — | 0.00 (0 of 31) | 0.00 (0 of 55) | 0.00 (0 of 24) |
| Single-node | lgcov root | 0.45 (20 of 44) | 1.00 (20 of 20) | 1.00 (44 of 44) | 1.00 (24 of 24) |
| Single-node | first drift + router rule | 0.39 (17 of 44) | 0.85 (17 of 20) | 0.86 (38 of 44) | 1.00 (24 of 24) |
| Single-node | edited node from the diff | 0.35 (20 of 57) | 1.00 (20 of 20) | 1.00 (44 of 44) | 1.00 (24 of 24) |
| Single-node | first divergence | 0.39 (17 of 44) | 0.85 (17 of 20) | 0.86 (38 of 44) | 1.00 (24 of 24) |
| Single-node | end-to-end | — | 0.00 (0 of 20) | 0.00 (0 of 44) | 0.00 (0 of 24) |

### One vote per edit

Cases sharing an edit aren't independent, so each edit also gets one vote, by majority across its questions. Each cell gives the rate, the count and a 95% Wilson interval.

| Edits | Method | Precision | Recall | Coverage | False flag |
|---|---|---|---|---|---|
| All | lgcov root | 0.57 (12 of 21; 0.37–0.76) | 1.00 (12 of 12; 0.76–1.00) | 1.00 (21 of 21; 0.85–1.00) | 1.00 (9 of 9; 0.70–1.00) |
| All | first drift + router rule | 0.52 (11 of 21; 0.32–0.72) | 0.92 (11 of 12; 0.65–0.99) | 0.90 (19 of 21; 0.71–0.97) | 1.00 (9 of 9; 0.70–1.00) |
| All | edited node from the diff | 0.50 (12 of 24; 0.31–0.69) | 1.00 (12 of 12; 0.76–1.00) | 1.00 (21 of 21; 0.85–1.00) | 1.00 (9 of 9; 0.70–1.00) |
| All | first divergence | 0.33 (7 of 21; 0.17–0.55) | 0.58 (7 of 12; 0.32–0.81) | 0.71 (15 of 21; 0.50–0.86) | 1.00 (9 of 9; 0.70–1.00) |
| All | end-to-end | — | 0.00 (0 of 12; 0.00–0.24) | 0.00 (0 of 21; 0.00–0.15) | 0.00 (0 of 9; 0.00–0.30) |
| Single-node | lgcov root | 0.47 (8 of 17; 0.26–0.69) | 1.00 (8 of 8; 0.68–1.00) | 1.00 (17 of 17; 0.82–1.00) | 1.00 (9 of 9; 0.70–1.00) |
| Single-node | first drift + router rule | 0.41 (7 of 17; 0.22–0.64) | 0.88 (7 of 8; 0.53–0.98) | 0.88 (15 of 17; 0.66–0.97) | 1.00 (9 of 9; 0.70–1.00) |
| Single-node | edited node from the diff | 0.42 (8 of 19; 0.23–0.64) | 1.00 (8 of 8; 0.68–1.00) | 1.00 (17 of 17; 0.82–1.00) | 1.00 (9 of 9; 0.70–1.00) |
| Single-node | first divergence | 0.41 (7 of 17; 0.22–0.64) | 0.88 (7 of 8; 0.53–0.98) | 0.88 (15 of 17; 0.66–0.97) | 1.00 (9 of 9; 0.70–1.00) |
| Single-node | end-to-end | — | 0.00 (0 of 8; 0.00–0.32) | 0.00 (0 of 17; 0.00–0.18) | 0.00 (0 of 9; 0.00–0.30) |

### Per node

`lgcov root` above is the label `lgcov ci` ships. It can name several nodes on one case, or none, so here each node it names root counts on its own. A case with only an inconclusive node names no root: a miss for recall, and not counted in precision.

- **Precision:** of the nodes named root, the share that are the edited node of a regressed case.
- **Recall:** of regressed cases, the share whose edited node is named root.
- **Exact match:** of regressed cases, the share where the roots named are exactly the edited node.
- **One side only:** of the nodes named root, the share named because only base or only the PR ran them.

| Edits | Precision | Recall | Exact match | Inconclusive cases | One side only |
|---|---|---|---|---|---|
| All | 0.56 (31 of 55) | 1.00 (31 of 31) | 1.00 (31 of 31) | 0 | 0.00 (0 of 55) |
| Single-node | 0.45 (20 of 44) | 1.00 (20 of 20) | 1.00 (20 of 20) | 0 | 0.00 (0 of 44) |

### What this says

- **Edited node from the diff matches or beats lgcov on recall and coverage on single-node edits:** recall 1.00 (20 of 20) against 1.00 (20 of 20), and coverage 1.00 (44 of 44) against 1.00 (44 of 44). Its false-flag rate is 1.00 (24 of 24). On precision lgcov is ahead by case, 0.45 (20 of 44) against 0.35 (20 of 57), because it stays quiet on an edit that changed nothing. By edit the two are level, as the table above shows.
- **Edits the diff can't pin to one node (T182) are scored on their own,** [below](#edits-the-diff-cant-pin-to-one-node): there the edited node from the diff has no single node to name.
- **lgcov flags harmless changes as drift roots (T262):** its drift-root false-flag rate is 1.00 (24 of 24) on single-node edits, against 1.00 (24 of 24) for the edited node from the diff. With the suite's code-check taxonomies, enforced-root false flags under `fail-on: harmed-root` are 0.25 (6 of 24), and measured recall is 0.83 (10 of 12) (rooted cases still unmeasured: 0.53 (29 of 55)). By taxonomy tag: requirement enforced-root false flags 0.17 (4 of 24), measured recall 0.82 (9 of 11) (unmeasured 0.62 (34 of 55)); format enforced-root false flags 0.08 (2 of 24), measured recall 1.00 (4 of 4) (unmeasured 0.78 (43 of 55)). A changed run lgcov names no root on is one it left inconclusive, not one it found harmless, and end-to-end's 0 only means it names no node.
- **On a router or fan-out edit lgcov names the routing node,** from the router's fingerprint, and first divergence names a node downstream: recall on all edits is 1.00 (31 of 31) against 0.55 (17 of 31). On single-node edits it is 1.00 (20 of 20) against 0.85 (17 of 20).
- **End-to-end names no node.** As a detector, its final answer changed on 1.00 (31 of 31) regressed cases and on 0.92 (22 of 24) harmless ones.

### Edits the diff can't pin to one node

T182: 30 cases from 10 distinct edits on 3 builds of one demo-shaped agent, [support.py](../examples/synthetic_agents/support.py) (10 regressed, 14 benign, 6 inert). In each edit a helper, the model name or a config default is read by 2 or more nodes, so the diff names no single node. `support-nested` runs respond inside a subgraph, the shape T220 found in open_deep_research. `support-client` keeps the model name in a client object built at import. These cases come from our own generator too: see [Limitations](#limitations).

Each case is scored against two truths:

- **Regression root:** a touched node where the edit, applied at that node only and with every other node as on base, makes the run fail the outcome check.
- **Drift root:** a touched node whose own output changes when the edit is applied at that node only, with every other node as on base.

lgcov's root label claims drift, not harm, since this run has no judges. So a harmless change lgcov names, like respond's on "Order status?", is a correct root under drift roots and a false root under regression roots.

`lgcov root` is the label `lgcov ci` ships, as above. The edited node from the diff is undefined here, since the diff names no single node, so it is scored as all touched nodes: every node whose code reaches the edit. First divergence names the first node whose output differs. Each node a method names counts on its own:

- **Precision:** of the nodes named, the share that are roots of their case.
- **Recall:** of the roots, the share named.
- **Exact match:** of the cases with roots, the share where the nodes named are exactly the roots.
- **False flag:** of the cases where the edit changed the run and nothing regressed, the share where a node is named. Against regression roots only, since a harmless drift is a correct drift root.
- **Fingerprint saw the edit:** for lgcov, of the nodes the edits touch, the share whose fingerprint changed, so that replay runs them live.

The counts are small, 10 regressed cases, so every ratio comes with them.

#### Against regression roots

| Edits | Method | Precision | Recall | Exact match | False flag | Inconclusive cases | Fingerprint saw the edit |
|---|---|---|---|---|---|---|---|
| Shared helper (support) | lgcov root | 0.29 (2 of 7) | 1.00 (2 of 2) | 0.50 (1 of 2) | 1.00 (4 of 4) | 0 | 1.00 (6 of 6) |
| Shared helper (support) | edited node from the diff: all touched nodes | 0.11 (2 of 18) | 1.00 (2 of 2) | 0.00 (0 of 2) | 1.00 (4 of 4) | 0 | — |
| Shared helper (support) | first divergence | 0.33 (2 of 6) | 1.00 (2 of 2) | 1.00 (2 of 2) | 1.00 (4 of 4) | 0 | — |
| Shared helper (support-nested) | lgcov root | 0.29 (2 of 7) | 1.00 (2 of 2) | 0.50 (1 of 2) | 1.00 (4 of 4) | 0 | 0.50 (2 of 4) |
| Shared helper (support-nested) | edited node from the diff: all touched nodes | 0.17 (2 of 12) | 1.00 (2 of 2) | 0.00 (0 of 2) | 1.00 (4 of 4) | 0 | — |
| Shared helper (support-nested) | first divergence | 0.17 (1 of 6) | 0.50 (1 of 2) | 0.50 (1 of 2) | 1.00 (4 of 4) | 0 | — |
| Model swap (support) | lgcov root | 0.50 (4 of 8) | 1.00 (4 of 4) | 1.00 (2 of 2) | 1.00 (4 of 4) | 0 | 1.00 (4 of 4) |
| Model swap (support) | edited node from the diff: all touched nodes | 0.33 (4 of 12) | 1.00 (4 of 4) | 1.00 (2 of 2) | 1.00 (4 of 4) | 0 | — |
| Model swap (support) | first divergence | 0.33 (2 of 6) | 0.50 (2 of 4) | 0.00 (0 of 2) | 1.00 (4 of 4) | 0 | — |
| Model swap (support-client) | lgcov root | 0.80 (4 of 5) | 1.00 (4 of 4) | 1.00 (2 of 2) | 1.00 (1 of 1) | 0 | 1.00 (2 of 2) |
| Model swap (support-client) | edited node from the diff: all touched nodes | 0.67 (4 of 6) | 1.00 (4 of 4) | 1.00 (2 of 2) | 1.00 (1 of 1) | 0 | — |
| Model swap (support-client) | first divergence | 0.67 (2 of 3) | 0.50 (2 of 4) | 0.00 (0 of 2) | 1.00 (1 of 1) | 0 | — |
| Shared config (support) | lgcov root | 0.40 (2 of 5) | 1.00 (2 of 2) | 0.00 (0 of 2) | 1.00 (1 of 1) | 0 | 1.00 (4 of 4) |
| Shared config (support) | edited node from the diff: all touched nodes | 0.17 (2 of 12) | 1.00 (2 of 2) | 0.00 (0 of 2) | 1.00 (1 of 1) | 0 | — |
| Shared config (support) | first divergence | 0.67 (2 of 3) | 1.00 (2 of 2) | 1.00 (2 of 2) | 1.00 (1 of 1) | 0 | — |
| All | lgcov root | 0.44 (14 of 32) | 1.00 (14 of 14) | 0.60 (6 of 10) | 1.00 (14 of 14) | 0 | 0.90 (18 of 20) |
| All | edited node from the diff: all touched nodes | 0.23 (14 of 60) | 1.00 (14 of 14) | 0.40 (4 of 10) | 1.00 (14 of 14) | 0 | — |
| All | first divergence | 0.38 (9 of 24) | 0.64 (9 of 14) | 0.50 (5 of 10) | 1.00 (14 of 14) | 0 | — |

#### Against drift roots

| Edits | Method | Precision | Recall | Exact match | Inconclusive cases |
|---|---|---|---|---|---|
| Shared helper (support) | lgcov root | 1.00 (7 of 7) | 1.00 (7 of 7) | 1.00 (6 of 6) | 0 |
| Shared helper (support) | edited node from the diff: all touched nodes | 0.39 (7 of 18) | 1.00 (7 of 7) | 0.17 (1 of 6) | 0 |
| Shared helper (support) | first divergence | 1.00 (6 of 6) | 0.86 (6 of 7) | 0.83 (5 of 6) | 0 |
| Shared helper (support-nested) | lgcov root | 1.00 (7 of 7) | 1.00 (7 of 7) | 1.00 (6 of 6) | 0 |
| Shared helper (support-nested) | edited node from the diff: all touched nodes | 0.58 (7 of 12) | 1.00 (7 of 7) | 0.17 (1 of 6) | 0 |
| Shared helper (support-nested) | first divergence | 0.17 (1 of 6) | 0.14 (1 of 7) | 0.00 (0 of 6) | 0 |
| Model swap (support) | lgcov root | 1.00 (8 of 8) | 1.00 (8 of 8) | 1.00 (6 of 6) | 0 |
| Model swap (support) | edited node from the diff: all touched nodes | 0.67 (8 of 12) | 1.00 (8 of 8) | 0.33 (2 of 6) | 0 |
| Model swap (support) | first divergence | 1.00 (6 of 6) | 0.75 (6 of 8) | 0.67 (4 of 6) | 0 |
| Model swap (support-client) | lgcov root | 1.00 (5 of 5) | 1.00 (5 of 5) | 1.00 (3 of 3) | 0 |
| Model swap (support-client) | edited node from the diff: all touched nodes | 0.83 (5 of 6) | 1.00 (5 of 5) | 0.67 (2 of 3) | 0 |
| Model swap (support-client) | first divergence | 1.00 (3 of 3) | 0.60 (3 of 5) | 0.33 (1 of 3) | 0 |
| Shared config (support) | lgcov root | 1.00 (5 of 5) | 1.00 (5 of 5) | 1.00 (3 of 3) | 0 |
| Shared config (support) | edited node from the diff: all touched nodes | 0.42 (5 of 12) | 1.00 (5 of 5) | 0.67 (2 of 3) | 0 |
| Shared config (support) | first divergence | 1.00 (3 of 3) | 0.60 (3 of 5) | 0.33 (1 of 3) | 0 |
| All | lgcov root | 1.00 (32 of 32) | 1.00 (32 of 32) | 1.00 (24 of 24) | 0 |
| All | edited node from the diff: all touched nodes | 0.53 (32 of 60) | 1.00 (32 of 32) | 0.33 (8 of 24) | 0 |
| All | first divergence | 0.79 (19 of 24) | 0.59 (19 of 32) | 0.46 (11 of 24) | 0 |

- **Against drift roots, lgcov's precision is 1.00 (32 of 32) and its recall 1.00 (32 of 32).** Inside a subgraph it names the node that ran there, by its path, since the base runs hold the subgraph's units, as `lgcov record --schema 2` writes them (T193).
- **Against regression roots, lgcov's precision is 0.44 (14 of 32).** Of the other 18 nodes it names, 18 are drift roots that did no harm: correct as drift, false as harm.
- **All touched nodes names 60 nodes,** with recall 1.00 (14 of 14) against regression roots. **First divergence names 24,** one per changed case, with exact match 0.50 (5 of 10) against regression roots.
- **End-to-end names no node.** As a detector, its final answer changed on 1.00 (10 of 10) regressed cases and on 1.00 (14 of 14) harmless ones.
- **Transitive-helper fingerprints aren't the gap:** here and in T220, a helper edit changed the fingerprint of every node lgcov fingerprints that reaches it.

### A/A re-runs

Each question re-run with no edit. These runs aren't in the case count. Label flips are base-vs-base attribution: the share of re-runs where any root or drifted label appears.

Label flip rate is 0.00 on a deterministic offline suite; this says nothing about live-model noise. Output and outcome flips stay beside it.

| Agent | Arm | Re-runs | Output flips | Outcome flips | Label flips | What it is |
|---|---|---|---|---|---|---|
| demo | re-record | 30 | 0.00 (0 of 30) | 0.00 (0 of 30) | 0.00 (0 of 30) | 0 by construction: a control, not a measurement |
| demo | served replay | 30 | 0.00 (0 of 30) | 0.00 (0 of 30) | 0.00 (0 of 30) | 0 by construction: a control, not a measurement |
| demo | seeded noise | 30 | 0.17 (5 of 30) | 0.13 (4 of 30) | 0.17 (5 of 30) | wiring test, noise rate set by us, not a real-world flip estimate |
| fanout | re-record | 30 | 0.00 (0 of 30) | 0.00 (0 of 30) | 0.00 (0 of 30) | 0 by construction: a control, not a measurement |
| fanout | served replay | 30 | 0.00 (0 of 30) | 0.00 (0 of 30) | 0.00 (0 of 30) | 0 by construction: a control, not a measurement |
| subgraph | re-record | 30 | 0.00 (0 of 30) | 0.00 (0 of 30) | 0.00 (0 of 30) | 0 by construction: a control, not a measurement |
| subgraph | served replay | 30 | 0.00 (0 of 30) | 0.00 (0 of 30) | 0.00 (0 of 30) | 0 by construction: a control, not a measurement |

### Noise rule

The noise rule (`drift_exceeds_noise`), on the seeded-noise agent at K=3: the recording and 3 repeats on base, 3 samples on head. `lgcov bench --noise-repeats` runs these repeats on an agent, on a real one once so far, with no change and no false alarm, and the gate still compares one sample. It names drift only when head's nearest sample is further from base than base's samples are from each other, so it misses a shift smaller than the spread. Below the threshold a node reads `no drift detected above noise (K=3)`: not cleared, since a small regression can sit inside the spread and the KPI judge decides that. The noise here is ours (1 answer in 5 is cut short), so these rates say how the rule behaves, not what a real model would give.

| Agent | Arm | Head's edit | Runs | Named drift | Reads as |
|---|---|---|---|---|---|
| demo | no change | base against base, so every flag is a false alarm | 200 | 0.01 (1 of 200) | false alarm |
| demo | small shift | the prompt becomes friendly and detailed | 200 | 0.34 (68 of 200) | caught; missed 0.66 (132 of 200) |
| demo | large shift | the prompt ends on a word the outcome check looks for | 200 | 0.56 (112 of 200) | caught; missed 0.44 (88 of 200) |

### Limitations

- Exact match on the edited node: parent-only naming counts as a miss, so an edit to `sub.inner` is a miss whatever a method says about `sub`.
- The base runs hold the units inside a subgraph, so lgcov labels as `lgcov ci` does on a baseline recorded with `lgcov record --schema 2` (T193; default since #208). On a baseline recorded without it, lgcov calls a subgraph inconclusive, since a subgraph has no fingerprint.
- A router edit counts as a hit whenever the router's code changed and a node drifted after it. lgcov doesn't check that the run took another branch, and no case here has a router edit beside a node edit.
- Fake models and small graphs, so these numbers don't predict a real PR.
- The regression label comes from each agent's own outcome check.
- The two order questions gave every edit the same label, so the case-level numbers count some edits more than their evidence supports.
- The T182 cases come from one demo-shaped agent with a fake model and the same three questions, so each edit counts up to three times.
- Not built for T182: a model name a node reads from an environment variable. The fingerprint doesn't read the environment, so it is predicted to change no fingerprint, and lgcov to see no drift.

Reproduce:

```bash
uv run lgcov bench --synthetic
```

This replaces only this section. Plain `lgcov bench` rewrites the whole file, so run this again after it. Held-out cases (T264) are opt-in and separate: `lgcov bench --synthetic --held-out` (does not mix into the suite above).

## Held-out

**20 of 20 harmless refactors are named root.** Under `fail-on: harmed-root`, enforced-root false flags are 0.20 (4 of 20); measured recall is 0.86 (18 of 21) (unmeasured rooted cases 0.45 (25 of 55)). By taxonomy tag: requirement enforced-root false flags 0.05 (1 of 20), measured recall 0.72 (13 of 18) (unmeasured 0.53 (29 of 55)); format enforced-root false flags 0.15 (3 of 20), measured recall 0.57 (8 of 14) (unmeasured 0.62 (34 of 55)).

Run 2026-11-22: 90 cases from 3 questions (2 recorded, 1 in-process) across 30 distinct edits on 3 offline agents, covering all 11 classes in [mutations.yaml](mutations.yaml) (41 regressed, 20 benign, 29 inert). Suite recordings are schema 2 (default since #208). No paid calls. These numbers never combine with the real PRs below.

- **Precision:** of the cases where a method names a node, the share where that node is the edited one and the edit regressed.
- **Recall:** of regressed cases, the share where the edited node is named.
- **Coverage:** of cases where the edit changed the run, the share where the edited node is named.
- **False flag:** of cases where the edit changed the run and nothing regressed, the share where a method names a node.

Single-node leaves out the router and fan-out edits, which change a conditional edge. All scores them against the edge's source node, the node that routes: its own output doesn't change, so a method that names it has to see the router's code.

### By case

| Edits | Method | Precision | Recall | Coverage | False flag |
|---|---|---|---|---|---|
| All | lgcov root | 0.64 (35 of 55) | 0.85 (35 of 41) | 0.95 (55 of 58) | 1.00 (20 of 20) |
| All | first drift + router rule | 0.50 (29 of 58) | 0.71 (29 of 41) | 0.84 (49 of 58) | 1.00 (20 of 20) |
| All | edited node from the diff | 0.46 (41 of 90) | 1.00 (41 of 41) | 1.00 (58 of 58) | 1.00 (20 of 20) |
| All | first divergence | 0.36 (21 of 58) | 0.51 (21 of 41) | 0.71 (41 of 58) | 1.00 (20 of 20) |
| All | end-to-end | — | 0.00 (0 of 41) | 0.00 (0 of 58) | 0.00 (0 of 20) |
| Single-node | lgcov root | 0.60 (30 of 50) | 0.91 (30 of 33) | 1.00 (50 of 50) | 1.00 (20 of 20) |
| Single-node | first drift + router rule | 0.42 (21 of 50) | 0.64 (21 of 33) | 0.82 (41 of 50) | 1.00 (20 of 20) |
| Single-node | edited node from the diff | 0.42 (33 of 78) | 1.00 (33 of 33) | 1.00 (50 of 50) | 1.00 (20 of 20) |
| Single-node | first divergence | 0.42 (21 of 50) | 0.64 (21 of 33) | 0.82 (41 of 50) | 1.00 (20 of 20) |
| Single-node | end-to-end | — | 0.00 (0 of 33) | 0.00 (0 of 50) | 0.00 (0 of 20) |

### One vote per edit

Cases sharing an edit aren't independent, so each edit also gets one vote, by majority across its questions. Each cell gives the rate, the count and a 95% Wilson interval.

| Edits | Method | Precision | Recall | Coverage | False flag |
|---|---|---|---|---|---|
| All | lgcov root | 0.63 (12 of 19; 0.41–0.81) | 0.86 (12 of 14; 0.60–0.96) | 0.95 (19 of 20; 0.76–0.99) | 1.00 (7 of 7; 0.65–1.00) |
| All | first drift + router rule | 0.50 (10 of 20; 0.30–0.70) | 0.71 (10 of 14; 0.45–0.88) | 0.85 (17 of 20; 0.64–0.95) | 1.00 (7 of 7; 0.65–1.00) |
| All | edited node from the diff | 0.47 (14 of 30; 0.30–0.64) | 1.00 (14 of 14; 0.78–1.00) | 1.00 (20 of 20; 0.84–1.00) | 1.00 (7 of 7; 0.65–1.00) |
| All | first divergence | 0.35 (7 of 20; 0.18–0.57) | 0.50 (7 of 14; 0.27–0.73) | 0.70 (14 of 20; 0.48–0.85) | 1.00 (7 of 7; 0.65–1.00) |
| All | end-to-end | — | 0.00 (0 of 14; 0.00–0.22) | 0.00 (0 of 20; 0.00–0.16) | 0.00 (0 of 7; 0.00–0.35) |
| Single-node | lgcov root | 0.59 (10 of 17; 0.36–0.78) | 0.91 (10 of 11; 0.62–0.98) | 1.00 (17 of 17; 0.82–1.00) | 1.00 (7 of 7; 0.65–1.00) |
| Single-node | first drift + router rule | 0.41 (7 of 17; 0.22–0.64) | 0.64 (7 of 11; 0.35–0.85) | 0.82 (14 of 17; 0.59–0.94) | 1.00 (7 of 7; 0.65–1.00) |
| Single-node | edited node from the diff | 0.42 (11 of 26; 0.26–0.61) | 1.00 (11 of 11; 0.74–1.00) | 1.00 (17 of 17; 0.82–1.00) | 1.00 (7 of 7; 0.65–1.00) |
| Single-node | first divergence | 0.41 (7 of 17; 0.22–0.64) | 0.64 (7 of 11; 0.35–0.85) | 0.82 (14 of 17; 0.59–0.94) | 1.00 (7 of 7; 0.65–1.00) |
| Single-node | end-to-end | — | 0.00 (0 of 11; 0.00–0.26) | 0.00 (0 of 17; 0.00–0.18) | 0.00 (0 of 7; 0.00–0.35) |

### Per node

`lgcov root` above is the label `lgcov ci` ships. It can name several nodes on one case, or none, so here each node it names root counts on its own. A case with only an inconclusive node names no root: a miss for recall, and not counted in precision.

- **Precision:** of the nodes named root, the share that are the edited node of a regressed case.
- **Recall:** of regressed cases, the share whose edited node is named root.
- **Exact match:** of regressed cases, the share where the roots named are exactly the edited node.
- **One side only:** of the nodes named root, the share named because only base or only the PR ran them.

| Edits | Precision | Recall | Exact match | Inconclusive cases | One side only |
|---|---|---|---|---|---|
| All | 0.64 (35 of 55) | 0.85 (35 of 41) | 0.85 (35 of 41) | 0 | 0.00 (0 of 55) |
| Single-node | 0.60 (30 of 50) | 0.91 (30 of 33) | 0.91 (30 of 33) | 0 | 0.00 (0 of 50) |

### What this says

- **Edited node from the diff matches or beats lgcov on recall and coverage on single-node edits:** recall 1.00 (33 of 33) against 0.91 (30 of 33), and coverage 1.00 (50 of 50) against 1.00 (50 of 50). Its false-flag rate is 1.00 (20 of 20). On precision lgcov is ahead by case, 0.60 (30 of 50) against 0.42 (33 of 78), because it stays quiet on an edit that changed nothing. By edit the two are level, as the table above shows.
- **This benchmark can't show where lgcov adds value.** lgcov's case rests on edits the diff can't pin to one node: a shared helper, a model or version swap, a shared config. Those are follow-up T182 and aren't here.
- **lgcov flags harmless changes as drift roots (T262):** its drift-root false-flag rate is 1.00 (20 of 20) on single-node edits, against 1.00 (20 of 20) for the edited node from the diff. With the suite's code-check taxonomies, enforced-root false flags under `fail-on: harmed-root` are 0.20 (4 of 20), and measured recall is 0.86 (18 of 21) (rooted cases still unmeasured: 0.45 (25 of 55)). By taxonomy tag: requirement enforced-root false flags 0.05 (1 of 20), measured recall 0.72 (13 of 18) (unmeasured 0.53 (29 of 55)); format enforced-root false flags 0.15 (3 of 20), measured recall 0.57 (8 of 14) (unmeasured 0.62 (34 of 55)). A changed run lgcov names no root on is one it left inconclusive, not one it found harmless, and end-to-end's 0 only means it names no node.
- **On a router or fan-out edit lgcov names the routing node,** from the router's fingerprint, and first divergence names a node downstream: recall on all edits is 0.85 (35 of 41) against 0.51 (21 of 41). On single-node edits it is 0.91 (30 of 33) against 0.64 (21 of 33).
- **End-to-end names no node.** As a detector, its final answer changed on 0.93 (38 of 41) regressed cases and on 0.70 (14 of 20) harmless ones.

### A/A re-runs

Each question re-run with no edit. These runs aren't in the case count. Label flips are base-vs-base attribution: the share of re-runs where any root or drifted label appears.

| Agent | Arm | Re-runs | Output flips | Outcome flips | Label flips | What it is |
|---|---|---|---|---|---|---|

### Limitations

- Exact match on the edited node: parent-only naming counts as a miss, so an edit to `sub.inner` is a miss whatever a method says about `sub`.
- The base runs hold the units inside a subgraph, so lgcov labels as `lgcov ci` does on a baseline recorded with `lgcov record --schema 2` (T193; default since #208). On a baseline recorded without it, lgcov calls a subgraph inconclusive, since a subgraph has no fingerprint.
- A router edit counts as a hit whenever the router's code changed and a node drifted after it. lgcov doesn't check that the run took another branch, and no case here has a router edit beside a node edit.
- Fake models and small graphs, so these numbers don't predict a real PR.
- The regression label comes from each agent's own outcome check.
- The two order questions gave every edit the same label, so the case-level numbers count some edits more than their evidence supports.

Reproduce:

```bash
uv run lgcov bench --synthetic
```

This replaces only this section. Plain `lgcov bench` rewrites the whole file, so run this again after it.
## Real PRs

A pilot, not a precision guarantee: one PR, one question, one run per side each time, and lgcov named one root each time. These numbers stay out of the sections above. Narrative tables below are written by hand; `### Metrics from results (offline)` is filled by `lgcov bench --fill-real-prs` (T101-B). The first hand run used `gpt-4.1`; the second, `gpt-6-luna`, is at the end.

### Metrics from results (offline)

Filled offline from `benchmark-results.json` (T101-B). Does not rewrite the hand narrative below. **Reenact contrast** here means the same *first divergence* baseline the synthetic tables already compare against — not a Reenact product hook.

| PR / benchmark | root precision | root recall | propagated precision | propagated recall |
|---|---|---|---|---|
| open_deep_research-163 | pending labels | pending labels | pending labels | pending labels |

TODO(T171): 2-3 more hand-labelled real PRs — blind packs checked in under
[benchmark/label-packs/](benchmark/label-packs/) (gemini #90/#35, local-deep-researcher #81);
fill `root` / `propagated` / `unaffected` there, then promote to yaml rows.

| | |
|---|---|
| Date | 2026-11-05 |
| lgcov commit | `8d9fd8b` |
| PR | [langchain-ai/open_deep_research#163](https://github.com/langchain-ai/open_deep_research/pull/163), `f95d200` to `6532a41` |
| Labels | [open_deep_research-163.yaml](benchmark/open_deep_research-163.yaml), reviewed by a human; its EU AI Act question only |
| Models | `gpt-4.1`; `gpt-4.1-mini` for summaries (pinned on both sides) and for compression on base (its default) |
| Settings | Clarification off, 1 research unit, 2 researcher iterations, 3 tool calls per researcher, to cap the cost |
| Versions | langgraph 1.2.16 and langchain-core 1.6.13, not the langgraph 0.6.2 the PR's lock pins |
| Validity | Checked before scoring: no tool or report errors in either side's saved run, and research notes and a final report on both |

### Benchmark KPIs

Measured by `lgcov bench`, apart from the last two rows.

| KPI | result | target |
|---|---|---|
| Root precision | 1.00 (1 of 1) | At least about 80%, the kill criterion |
| Root recall | 0.17 (1 of 6) | None; capped at about 1/6 |
| Root F1 | 0.29 | None |
| Propagated precision | 0.50 (1 of 2) | None |
| Propagated recall | 1.00 (1 of 1) | None |
| Propagated F1 | 0.67 | None |
| Time | 216 s, both sides | None |
| Spend | $0.52 (errors not recorded; see [Cost per run](#cost-per-run)) | At most $0.60; stop and re-plan over $1 |
| Requests | Not recorded (see [Cost per run](#cost-per-run)) | About 110 |

lgcov said root `write_research_brief`, propagated `final_report_generation` and `research_supervisor`.

- **Recall is capped at about 1/6.** This run called only the first node to drift root, and this PR has 6 labelled roots. T162 changed that rule, but on these runs it names no root: see [Multi-root re-score](#multi-root-re-score-t162).
- **The propagated false positive comes from the same limit.** `research_supervisor` is the subgraph holding the other 5 labelled roots, and lgcov scores it as one node.
- **Root precision is 1 of 1,** so it says little: the 95% Wilson interval is 0.21 to 1.00.
- **The hit has a real cause, but this run can't separate cause from noise.** The supervisor's system prompt, which the PR rewrote, differs between the sides. But a node whose code changed runs live, and a live model answer differs anyway. A later base-against-base run gave the same labels, so this hit can't be told apart from noise: see [A/A noise floor](#aa-noise-floor).
- **Clarification off removes the likeliest false root.** `clarify_with_user` only renamed locals, and with clarification off it calls no model, so it can't drift. With it on, it would run live and probably be called root. That wasn't measured.

### Multi-root re-score (T162)

T162 calls a node root when its own fingerprint changed and it drifts when run with head's code on base's recorded input. That run is a probe with the network blocked, so it costs nothing. A node that reaches for the network would need a live call, and the [A/A run](#aa-noise-floor) shows one live sample drifts with no change, so it's inconclusive. `lgcov bench --noise-repeats K` (T211) repeats it on both sides and names it root only above base's own spread; the gate still treats a changed model node as inconclusive until base samples are in the cassette. The first node to drift is still reported, as `first_drift`.

Scored from the pilot's saved runs with `lgcov bench docs/benchmark --only open_deep_research-163 --from-runs <stage1/runs>`, no call made. Saved runs can't be probed, so every changed node that drifted is inconclusive here:

| | first drift (before) | T162 (after) |
|---|---|---|
| Root precision / recall / F1 | 1.00 / 0.17 / 0.29 | no root named / 0.00 / 0.00 |
| Propagated precision / recall / F1 | 0.50 / 1.00 / 0.67 | no node named / 0.00 / 0.00 |
| Root | `write_research_brief` | none |
| Propagated | `final_report_generation`, `research_supervisor` | none |
| Inconclusive | | `write_research_brief`, `final_report_generation` (not probed), `research_supervisor` (no fingerprint) |

- **Five of the six roots can't be reached by any rule yet.** They're inside the `research_supervisor` subgraph, whose inner nodes aren't recorded (T193).
- **`write_research_brief` is a model node.** After masking, it gets the same input on both sides and drifts, but that drift is one live sample. A probe would need a live call, so from saved runs it stays inconclusive; `--noise-repeats` hasn't been run on this agent.
- **`final_report_generation` is now inconclusive, not propagated.** Its code changed and its input changed, and without a probe on base's input lgcov can't say which one made it drift.
- **A node function's docstring, comments and formatting no longer count as a change (T212).** A docstring something could read still does: see [which nodes replay re-runs](../README.md#which-nodes-replay-re-runs) for the cases. Checked offline, imports only, on this PR and three labelled PRs that aren't rows here yet:

  | PR | Function nodes | Changed, raw lines | Changed, syntax tree | Changed, with the docstring rule |
  |---|---|---|---|---|
  | open_deep_research#163 | 3 | 3 | 3 | 3 |
  | gemini-fullstack-langgraph-quickstart#90 | 4 | 1 | 1 | 1 |
  | gemini-fullstack-langgraph-quickstart#35 | 4 | 2 | 2 | 1 |
  | local-deep-researcher#81 | 5 | 3 | 3 | 3 |

  One node flips: `generate_query` in #35, whose only edit is its docstring. No label on #163 changes. Ignoring comments and formatting alone changed nothing on these four.

### Agent KPIs

From [taxonomy.yaml](taxonomy.yaml). `lgcov bench` doesn't judge these, so the two code checks were read from the saved runs by a script using the same pass rule. `lgcov judge` replays the graph, which would have meant more paid calls. With one case and one run per side, these aren't gate verdicts.

| KPI | what it checks | judge | base | head |
|---|---|---|---|---|
| `supervisor_delegates` | The supervisor calls `ConductResearch` at least once | code | Pass (1 call) | Pass (1 call) |
| `researcher_uses_think_tool` | The researcher calls `think_tool` at least once | code | Fail (0 calls) | Pass (1 call) |
| `report_claims_supported` | The report contradicts nothing in the research notes, and every substantive claim is backed by them | model | Not judged | Not judged |

- **`researcher_uses_think_tool` can't pass on base,** because this PR is what added `think_tool`. Head's count comes from the one "Reflection recorded" line in its research notes, because the researcher's own messages aren't saved.
- **`report_claims_supported` needs a paid model call,** so it wasn't run.

### Second run, on `gpt-6-luna`

The same PR and question on a cheaper model, because `gpt-4.1` and `gpt-4.1-mini` were ruled out on cost. It's still one PR, and lgcov named the same node, so this isn't a second data point for precision.

| | |
|---|---|
| Date | 2026-11-05 |
| lgcov commit | `8d9fd8b` |
| Models | `gpt-6-luna` for research, compression, the final report and summaries, pinned on both sides |
| Output caps | 32,000 tokens for compression and the final report; the other settings as in the first run |
| Labels | As in the first run, except that `compress_research` is propagated for this run, not root. Pinning its model removes its only evidence, and its prompts are the same on both sides. The labels file is unchanged. |
| Validity | Checked before scoring: no tool or report errors in either side's saved run, no empty notes, and research notes and a final report on both. The saved answers all came from `gpt-6-luna`. |

| KPI | this run | first run | target |
|---|---|---|---|
| Root precision | 1.00 (1 of 1) | 1.00 (1 of 1) | At least about 80%, the kill criterion |
| Root recall | 0.20 (1 of 5) | 0.17 (1 of 6) | None; capped at about 1/5 |
| Root F1 | 0.33 | 0.29 | None |
| Propagated precision | 0.50 (1 of 2) | 0.50 (1 of 2) | None |
| Propagated recall | 0.50 (1 of 2) | 1.00 (1 of 1) | None |
| Propagated F1 | 0.50 | 0.67 | None |
| Time | 538 s, both sides | 216 s, both sides | None |
| Spend | About $0.16 ([how](#cost-per-run)) | $0.52 | Under $0.60 |
| Requests | Not recorded (see [Cost per run](#cost-per-run)) | Not recorded (see [Cost per run](#cost-per-run)) | None |

lgcov said root `write_research_brief`, propagated `final_report_generation` and `research_supervisor`, the same as the first run.

- **The changes in the scores come from the label move, not from lgcov.** It named the same nodes. Propagated recall fell because `compress_research` is inside the subgraph, where lgcov can't name a node.
- **Root precision is 1 of 1 again,** on the same PR and the same node, so it isn't 2 of 2. The 95% Wilson interval is still 0.21 to 1.00.
- **The first run's other limits still apply:** nothing here separates cause from noise, and clarification was off.
- **Head researched far less than base:** 1 block of search results and 19 sources, against 8 and 107. The first run leaned the same way, at 1 and 23 against 4 and 42. One run can't say whether that's the PR's new limits.
- **An earlier try on this model was invalid, and isn't scored here.** At the default cap of 8,192 tokens, base's compression came back empty, so its final report was written with no notes, and the run still produced a score. The most likely cause is that reasoning used up the cap, since reasoning tokens count toward it. That's why the caps above are higher, and why the validity check now fails on an empty note.

| Agent KPI | base | head |
|---|---|---|
| `supervisor_delegates` | Pass (1 call) | Pass (1 call) |
| `researcher_uses_think_tool` | Fail (0 calls) | Pass (2 calls) |
| `report_claims_supported` | Not judged | Not judged |

These were read the same way as the first run's, and `researcher_uses_think_tool` still can't pass on base.

### A/A noise floor

#163's base, `f95d200`, run against itself with `lgcov bench --live`, so head ran every node live and nothing was served. No code differs, so any label is noise. Same question and settings as the `gpt-6-luna` run above; written down before the run: pass if lgcov labels no root or reports INCONCLUSIVE, fail on any root.

| Date | lgcov commit | Validity | Root | Propagated | Verdict | Time | Spend |
|---|---|---|---|---|---|---|---|
| 2026-11-10 | `98c6587` | Valid; all saved answers from `gpt-6-luna` | `write_research_brief` | `final_report_generation`, `research_supervisor` | Fail | 757 s, both sides | $0.13 |

- **These are exactly the labels both #163 runs gave.** `write_research_brief` is the first node that calls a model, and drift is an exact match, so it's called root whenever it runs live, PR or no PR. #163's 1 of 1 is a root by construction, not evidence that lgcov found the cause.
- **So the kill criterion can't be read from real PRs yet.** With no change, the false-root rate on this agent is 1 of 1. On a PR that makes the first model node run live, root precision only shows whether the labels include that node.
- **The drift has two sources.** The model wrote a different brief (1,021 against 1,280 characters), and the searches differed: head found 140 sources to base's 72. `research_supervisor` also keeps response ids inside text (`rs_…`, `fc_…`, `call_…`), which lgcov didn't mask at the time, so it drifted even when the model's text matched; a free run on a stub showed that. lgcov masks those ids since T213, and the `encrypted_content` blob beside them since T213b.
- **A fix is proposed, not built:** before calling a node root, run it again on base with the same recorded input, and report INCONCLUSIVE if base against base already drifts there. With exact-match drift that makes lgcov honest about model nodes but can't name one as root; that needs a distance measure and a threshold over several base samples.

### A/A at K=3

The same base-against-base row and settings, with `lgcov bench --live --noise-repeats 3` (T232): the first node to drift runs 3 more times on each side, and the [noise rule](#noise-rule) reads the result. A root needs a changed fingerprint and none changes here, so "no root" is true by construction and proves nothing. What the run measures is the noise check: with no change, does it say drift? Written down before the first run: pass if the check on a model node, at `k` 3, reads `no drift detected above noise (K=3)` and no root is named; fail on `drift above noise (K=3)` or any root; inconclusive if the check ran on no model node. An invalid run has no verdict.

| Date | lgcov commit | Validity | Node checked | Spread | Head's nearest to base | Root | Verdict | Time | Spend |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-14 | `16c1b2b` | Invalid: one search timed out on base | not read | not read | not read | not read | None | 517 s, both sides | $0.09 |
| 2026-11-14 | `16c1b2b` | Valid; all saved answers from `gpt-6-luna` | `write_research_brief`, `k` 3 | 0.234 | 0.149 | None | Pass | 577 s, both sides | $0.34 |

- **One trial of the false-alarm rate.** The rule gave no false alarm on one real-model A/A at K=3. One run can't give a rate.
- **It doesn't test the other direction.** Nothing changed, so the run can't show that the rule catches a real change; that needs a run with a changed node. Node cause on a real model is still unproven.
- **`research_supervisor` stayed inconclusive.** It has no fingerprint (T193), so no check ran on it, and `final_report_generation` was labelled propagated with no change.
- **The second run added one setting.** After the first run, a timed-out search is tried once more on both sides. The pass rule and the validity check didn't change. No search timed out on the second run, so the retry never ran.
- **The gate uses recorded samples when the baseline has them (T229).** Without `--samples K` at record time it still compares one sample.

## Baselines

### Comparison points

- **The base recording.** Each run compares head with base's run of the same question, at `f95d200`, one run per side.
- **The hand labels.** They were drafted from the PR's diff and reviewed by a human ([labels](benchmark/open_deep_research-163.yaml)). On the `gpt-6-luna` run, `compress_research` counts as propagated, not root, because pinning its model removes its only evidence.
- **Base against base.** The [A/A noise floor](#aa-noise-floor) run gave the same labels as #163, so nothing here separates the PR's effect from a live model's own variation.

### Naive methods

Scored on #163 from what was already on disk, the saved runs and a clone of the PR, with no paid calls. They use the same labels as lgcov, including the label move above. Each cell gives the pilot, then the `gpt-6-luna` run.

| Method | What it calls root | Root precision | Root recall | Propagated recall |
|---|---|---|---|---|
| lgcov | The first node to drift; later ones are propagated | 1 of 1, 1 of 1 | 1 of 6, 1 of 5 | 1 of 1, 1 of 2 |
| Edited node from the diff | Each node whose own function differs between the commits, ignoring formatting | 6 of 8, 5 of 8 | 6 of 6, 5 of 5 | 0 of 1, 0 of 2 |
| First divergence | The first node whose saved output differs | 1 of 1, 1 of 1 | 1 of 6, 1 of 5 | 0 of 1, 0 of 2 |
| End-to-end (final answer changed) | No node | Not scored | Not scored | Not scored |

- **Edited node from the diff beats lgcov on recall here, but picks nothing out.** This PR edits all 8 node functions, so calling every node root scores the same. Its false roots are `clarify_with_user` (renamed locals), `final_report_generation` (matters only on a retry, an error or a different key) and, on the second run, `compress_research`. The labels were drafted from the same diff, which favours this method.
- **First divergence ties lgcov on root in both runs, because first drift is lgcov's root rule.** On this PR, lgcov's root adds nothing over it. What lgcov adds is its propagated labels, at a precision of 1 of 2 in both runs.
- **End-to-end isn't scored because it names no node.** The final report differs in both runs, but there's no node to check against the labels, and the base-against-base run's final report differs too, so we can't say the difference comes from the PR.

On single-node synthetic edits, edited node from the diff matches or beats lgcov on recall and coverage, and lgcov is ahead only on case-level precision: see [Synthetic](#synthetic). lgcov's case rests on changes the diff can't pin to one node: those are [measured on synthetic cases](#edits-the-diff-cant-pin-to-one-node), not yet on a real PR.

## What the tool missed

Seen in a benchmark run:

- **Roots it can't check (T162).** Since T162 every node whose own code changed and that drifts on base's recorded input is root, not just the first to drift: the `multi_root` demo fixture scores 2 of 2. On #163 it names none. Five of the six roots are inside a subgraph, which isn't recorded (T193), and checking `write_research_brief` would need a live model call, which one sample can't tell from noise (T211), so it's inconclusive.
- **Nodes inside a subgraph.** A subgraph is scored as one node. On #163 that made `research_supervisor` a propagated false positive, and it holds most of the labelled roots. The [demo fixture](#known-gaps) misses `sub.inner` the same way.

Known from the [replay audit](validation/replay-audit.md#matrix), with no benchmark case yet:

- **A root named one node late.** If a node's output changes only in an `id`, UUID or date-time, it shows no drift, and the first node whose output changes because of it is called root.

## Cost per run

From the OpenAI usage page: $0.84 in total for the 3 runs on 5 November. The page's total doesn't split the two `gpt-6-luna` runs, so their figure is the average of what's left after the pilot.

| Run | Cost | Pending |
|---|---|---|
| Pilot, `gpt-4.1` | $0.52 | Requests, tokens, errors |
| `gpt-6-luna`, invalid and not scored | About $0.16 | Requests, tokens, errors |
| `gpt-6-luna`, scored | About $0.16 | Requests, tokens, errors |
| A/A noise floor, `gpt-6-luna`, 10 November | $0.13 | Requests, tokens, errors |
| A/A at K=3, `gpt-6-luna`, 14 November, invalid | $0.09 | Requests, tokens, errors |
| A/A at K=3, `gpt-6-luna`, 14 November, valid | $0.34 | Requests, tokens, errors |

## Rounds

A [benchmark round](benchmark-rounds.md) locks a run's commits and cases, and an exposure ledger records which cases each candidate has seen. `lgcov round check` fails if a locked SHA moved or a case was already exposed to that candidate in another round. [The #163 round](benchmark/rounds/t101-163/lgcov-round.yaml) is locked retroactively: its runs came first.
