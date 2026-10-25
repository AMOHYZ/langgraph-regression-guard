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
    judge: jev                  # jev | code | fake
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
- `judge` must be `jev`, `code`, or `fake`. `code` raises
  `NotImplementedError("code judges arrive in T072")` until T072.
- Malformed YAML, or a top level that is not a mapping, is a `ValueError`
  naming the file (never a raw `yaml` error).
- `label_hints` keys must be a subset of `labels`.
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
