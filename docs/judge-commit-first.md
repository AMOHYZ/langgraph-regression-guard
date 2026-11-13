# Commit-first judge mode (spec, T157)

**Status: spec only.** Nothing here is built. This file adds no prompt code, no taxonomy
field and no paid run. It says what to build and how we would know it worked.

## The idea

Today a model judge gets a node's input and output in one request and picks a label
([`jev_judge.py`](../src/lgcov/jev_judge.py)). It reads the candidate's answer before it has
one of its own, so it can grade how plausible the answer sounds, not whether it is right.

In commit-first mode the judge works in two steps:

1. **Commit.** The judge gets the case and writes down its own answer. It does not see the
   candidate's output.
2. **Grade.** Only then does it see the candidate's output, and it grades that against the
   answer it already wrote.

Zhou reports a judge's false-positive rate falling from 0.719 to 0.012 with this change, and
attributes the drop to the commit step, not to hidden anchors
([arXiv:2607.05904](https://arxiv.org/abs/2607.05904), v1 of 7 Jul 2026, a preprint with one
author; abstract and section 5.2, read 13 Nov 2026). That number is theirs, on their setup: one
judge model (Qwen3-4B) on 128 cases. This repo has not reproduced it.
[Section 4](#4-measuring-the-aa-false-positive-rate) says how we would measure our own.

## 1. Which KPIs qualify

Commit-first applies only to a KPI that has an independent check: something that says what the
right answer is without asking a model. The judge never sees that check. It is what we compare
the judge against ([section 3](#3-logging-agreement-with-the-independent-check)).

| KPI has | Qualifies | Why |
|---|---|---|
| A reference answer the case fixes before the run | Yes | The judge's own answer can be checked against it |
| An executable check on the answer | Yes | The check gives pass or fail with no model |
| Only a rubric (tone, helpfulness, "delegates research") | No | The judge's own answer would be one more opinion, with nothing to check it against |
| A reference taken from the candidate's own output | No | It isn't independent of what is being graded |

- **Reference answer:** a value known before the candidate runs. It can sit in the case's
  input, or in a recorded step upstream of the judged node that replay serves unchanged.
- **Executable check:** a deterministic rule on the answer, such as "contains the order's
  status". The `check:` kinds a `judge: code` KPI uses today read the run's nodes and tools,
  not the answer's text, so an answer check is new work.
- **Code judges stay as they are.** A KPI that a code check can decide fully needs no model,
  so no commit step.

A taxonomy that asks for commit-first on a KPI with neither a reference nor a check should be
refused at load time, naming the KPI, as the other taxonomy errors do
([taxonomy.md](taxonomy.md)). The field names are left to the implementation.

**In the demo taxonomy** ([`examples/demo_agent/taxonomy.yaml`](../examples/demo_agent/taxonomy.yaml))
one KPI qualifies: `answer_states_order_status`. Its reference is the order's status that
`lookup_order` returns, which is in `respond`'s recorded input. `respond_runs` is a code judge
and stays one.

## 2. Prompt shape

Two requests per judgment, never one. A single request that says "answer first, then look"
does not hide the candidate's output from the model.

**Step 1, commit (blind)**

- Sees: the judged node's input, as recorded, and the KPI's question put as a task ("what is
  the status of this order?").
- Does not see: the node's output, the reference answer, or the labels.
- Returns: its own answer, or "cannot answer from this input".

**Step 2, grade**

- Sees: the answer from step 1, marked as fixed, the candidate's output, the rubric and the
  labels.
- Does not see: the reference answer.
- Returns: a label, a confidence, and whether the candidate's output agrees with the committed
  answer.

Rules for the implementation:

- **No shared conversation.** Step 2 is a new request that quotes step 1's answer. The judge
  cannot revise the commit.
- **The blind step can't be handed the output by mistake.** Build step 1's request with a
  function that takes no node output.
- **The commit is recorded before step 2 is sent:** its text and a SHA-256 of it, kept with the
  verdict's raw data.
- **Same pinned model for both steps,** the `jev_model` in `lgcov.yaml`.
- **Cannot answer:** the judgment is marked `no_commit`, graded in one step as today, and
  counted on its own line. It never counts as agreement.
- **Cost:** two model calls per judgment, so judge calls and judge cost double for these KPIs.
  Budgets and the report's call counts must show both.
- **Not comparable with one-step verdicts.** A verdict records which mode made it. Calibration
  labels and bands are per mode: a band earned in one-step mode does not carry over.

## 3. Logging agreement with the independent check

Each commit-first judgment writes one record:

| Field | Meaning |
|---|---|
| `kpi`, `case`, `side`, `sample` | Which judgment: the KPI, the case, base or PR, and the repeat |
| `model`, `mode` | The pinned judge model, and `commit_first` or `no_commit` |
| `commit_sha256` | Hash of the step 1 answer |
| `commit_matches_check` | The judge's own answer passes the independent check |
| `label` | The judge's step 2 label |
| `check_label` | The independent check applied to the candidate's output |
| `label_matches_check` | `label` and `check_label` agree |

Two rates per KPI, each with its count and a 95% Wilson interval, as the rest of lgcov reports
rates:

- **Commit accuracy:** the share of judgments where `commit_matches_check` is true. A judge
  that can't answer the case itself has nothing sound to grade against.
- **Verdict agreement:** the share where `label_matches_check` is true, with the 2 by 2 table
  behind it (judge pass or fail against check pass or fail). Judge fail with check pass is a
  false positive. Judge pass with check fail is a miss.

Where it shows: one line per commit-first KPI in `lgcov judge`'s output and in the PR comment's
judge table, such as "agrees with check: 58 of 60". `no_commit` judgments are listed beside it
and left out of both rates.

What it does not replace: agreement with a check is not agreement with a person. `lgcov
calibrate` against human labels still decides whether a judge may block
([gate.md](gate.md)).

## 4. Measuring the A/A false-positive rate

On the demo taxonomy's reference-anchored KPI, `answer_states_order_status`.

- **A/A pair:** the same recorded run judged as base and as PR. Nothing changed, so every
  difference the judge reports is the judge's own.
- **Arms:** one-step (today's mode) and commit-first, on the same cases, the same pinned
  model and the same number of repeats.
- **Cases:** the demo agent's order questions, where the reference exists. The number of
  judgments per arm is fixed before the run.
- **Independent check:** the answer contains the order's status. The synthetic suite's outcome
  check for the demo agent already uses this rule.

What gets reported, per arm:

| Number | Definition |
|---|---|
| Judgment false-positive rate | Judge says fail where the check says pass, over judgments where the check says pass |
| Pair flip rate | Base and PR get different labels, over A/A pairs |
| Gate false-positive rate | Sets of A/A pairs on which the gate would BLOCK or flag, over all sets |
| Commit accuracy | As in section 3, commit-first arm only |
| Cost | Model calls and spend per arm |

Each rate comes with its count and a 95% Wilson interval, the model, and the date.

- **How many judgments:** telling 0.7 from 0.01 takes few. Bounding a rate near 0.01 takes
  more: 0 false positives in 300 judgments gives a 95% upper bound of about 0.013.
- **Offline first:** the two-step flow, the records and the report can be built and tested
  with a fake two-step judge and no network. That tests the plumbing and measures nothing.
- **The real number needs a paid run,** which needs the maintainer's explicit OK each time and
  is not part of this spec's PR.

Limits of this measurement:

- The demo agent's model is fake and its graph has 3 nodes, so the result describes the judge
  on easy cases, not on a real agent.
- One KPI. A rate from it says nothing about KPIs with a harder reference.
- A/A measures false positives only. It can't show whether commit-first misses more real
  regressions: that needs cases with a known regression, such as the synthetic suite's
  regressed cases.

## Open questions

- A floor for commit accuracy below which a KPI drops to advisory, and who sets it.
- A cap on the share of `no_commit` judgments before a KPI stops qualifying.
- Whether `tool_called`-style checks should count as an independent check for a model judge on
  the same KPI, or whether such a KPI should simply be a code judge.
