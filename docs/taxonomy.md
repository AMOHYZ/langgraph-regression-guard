# Taxonomy schema (version 1)

A taxonomy turns "what can silently break" into judges. It lists the failure
modes of one system, then one or more KPIs per mode; each KPI becomes exactly
one judge that reads one node's I/O from a cassette (`T062`).

## File layout

```yaml
version: 1                      # must be 1
system: open_deep_research      # name of the graph under test
failure_modes:
  - id: supervisor_drift        # snake_case, referenced by kpis
    description: what goes wrong
    evidence:                   # optional trace/PR refs
      - PR #163
kpis:
  - name: delegates_research    # snake_case, unique
    failure_mode: supervisor_drift
    node: supervisor            # graph node the judge reads, or "final"
    judge: jev                  # jev | code | fake | llm
    description: one line
    rubric: the question the judge answers
    labels: [pass, fail]            # optional, default binary
    label_hints: {pass: ..., fail: ...}  # optional
    score_map: {pass: 1.0, fail: 0.0}     # optional, default for binary
    check: {type: tool_not_called, tool: ...}  # only for judge: code (T072)
```

## Validation (load-time, all raise ValueError)

- `version` must be `1`.
- KPI `name`s must be unique; `name`, `failure_mode`, `node`, `judge`,
  `description`, `rubric` are required.
- `failure_mode` must reference a declared failure-mode `id`; the error names
  the offending KPI.
- `judge` must be `jev`, `code`, `fake` or `llm`. A `code` judge (T072) decides with
  the rule in `check`: `node_visited`, `tool_called` or `tool_not_called`. A `jev` judge
  calls the Jev service (`JEV_API_URL`, `JEV_API_KEY`); `fake` is the offline stand-in;
  `llm`, a general LLM judge, runs in the nightly tier only (T089).
- Bands (T063): code judges always block. A model judge's band (blocking, flag or
  advisory) comes from `lgcov calibrate` against human labels; with no labels it is
  advisory (`gate.uncalibrated: flag` makes it a flag).
- Malformed YAML, or a top level that is not a mapping, is a `ValueError`
  naming the file (never a raw `yaml` error).
- `label_hints` keys must be a subset of `labels`.
- `review` (T106), on a failure mode or a KPI, must be `pending` or `approved`; no
  `review` means a human wrote it. A file with a pending entry is refused: "N entries
  pending human review; approve with `lgcov taxonomy approve <id>`". Drafted entries
  also carry `evidence` (trace ids), `why`, `signature` (failure modes) and, once
  approved, `reviewer`.
- Custom (non-binary) `labels` need an explicit `score_map`; only binary
  pass/fail gets defaults.
- Omitted `labels`/`score_map` default to binary pass/fail (1.0/0.0); see
  `KPI`/`KPI.binary` in `lgcov.judges` for label and score validation.

## From file to judges

`load_taxonomy(path)` parses the YAML (`yaml.safe_load`). `Taxonomy.kpis()`
turns the specs into `KPI` objects. `judges_from_taxonomy(tax, factories)`
builds one judge per KPI — keyed by KPI name so the runner knows which node
each judge reads — using the factory registered for that `judge` kind.

## CLI

```bash
lgcov taxonomy check examples/open_deep_research/taxonomy.yaml
```

Validates the file and prints the KPI table (name, node, judge, labels).
Exit 0 when valid, 2 when invalid.
