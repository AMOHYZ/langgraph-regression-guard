# Changelog

## 0.1.0 (unreleased)

The first release: what was built from 25 to 31 October 2026, by layer, one line per
feature, with its PR.

### L1 — Cassettes

- `lgcov --version`, `lgcov record`, and a quickstart that starts with the install (#10).
- `lgcov replay`: drifted nodes against the recorded cassettes, root vs propagated (#6).
- Volatile fields (UUIDs, timestamps, ids, `ignore_paths`) masked before comparing (#12).
- Unchanged nodes served from the cassette: replay runs only what a change touched (#18).
- Drift gate (`--fail-on root-drift|any-drift|never`) and `lgcov snapshot` (#16).
- Node and edge coverage, with the trend against main, in the PR comment (#20, #21).
- Outcomes PASS / AGENT_FAIL / INFRA_ERROR: a node that raises is the agent's, network
  trouble isn't (#24); `lgcov selfcheck` proves the classification on fixtures (#25).
- Divergence policy, a time budget and a timing line; `pr` and `nightly` tiers (#28).
- `thresholds.min_node_coverage` gates `lgcov ci` (#30).
- A router, or a node inside a subgraph, that raises is named like a node (#35), and so
  is a router that returns a key its mapping doesn't have (#39).
- `lgcov lint`: unreachable nodes, routers and edges naming missing nodes, nodes no
  cassette reached, nodes no KPI names; warnings only (#36).

### L2 — Judge gate

- Deterministic code judges: node visited, tool called, tool not called (#2).
- A failure-mode taxonomy for open_deep_research's PR #163, one binary KPI per mode (#14),
  its names checked against the PR's merge commit (#38).
- `lgcov taxonomy draft` proposes failure modes and KPIs from traces; `lgcov taxonomy
  approve` is a human's yes, and a pending entry can't gate (#38).
- pass@k, pass^k, flakiness and cost per KPI (#22); `lgcov judge`, with a call guard (#23).
- Paired significance statistics (#26) and the gate: SHIP, BLOCK or INCONCLUSIVE per KPI,
  in `lgcov judge` and the Action (#27).
- `lgcov calibrate`: TPR/TNR with Wilson intervals and Cohen's κ against human labels;
  calibration bands decide which judges can block (#32).
- KPI results per node and per slice; the overall verdict is the worst group (#34).

### L3 — Review loop

- `lgcov cassette accept <node>...` re-baselines only the nodes a reviewer accepts (#11).
- `lgcov review override`: an override becomes a label, and bands move on the next PR,
  through git (#33).

### The Action and the PR comment

- A Markdown PR report with a golden test (#1); a graph-diff comment on PRs (#3), updated
  in place rather than added again (#4).
- `lgcov.yaml`, a typed config that replay and the Action read (#9).
- A reusable composite Action, dogfooded on this repo (#17); its exit-code steps survive
  Actions' `bash -e` (#19).

### Benchmarks and docs

- The demo agent: 3 nodes, 1 tool, 1 conditional edge, a fake LLM (#5).
- `docs/why-us.md`: what lgcov does that others don't, with an honest status each (#15,
  #29, #34).
- Speed benchmark: served vs `--live` on the demo PR (#31).
- `lgcov bench`: the attribution benchmark, on synthetic fixtures (#34).
- The first real benchmark row, open_deep_research PR #163, labels pending human review
  (#38).
- The README roadmap lists only what isn't built (#37).

### Packaging and release

- This changelog, an Install section, and `release.yml` by hand as a dry run that builds
  the wheel and checks it runs (#40).
- The package is named `lgcov`, like its command; the repo keeps its name (#41).
