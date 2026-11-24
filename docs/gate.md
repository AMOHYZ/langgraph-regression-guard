# What the gate guarantees

SHIP needs every blocking KPI (K_gate of them) to pass its own non-inferiority test: its
95% CI rules out a drop of δ. Since all of them must pass, a wrong SHIP is no likelier than
one KPI's wrong pass, 2.5% (one side of the 95% CI), however many KPIs there are. That makes
SHIP an intersection-union test, which needs no multiplicity correction. BLOCK is a claim
about one KPI, and every blocking KPI is another chance of a false alarm, so BLOCK p-values
are Holm-corrected across all K_gate: the chance of even one false BLOCK stays at α. A node
or slice group never goes further than its KPIs' own verdicts.

- **K_gate** is the number of KPIs the run judges that `lgcov.yaml` (`gate.blocking_kpis`
  or `gate.advisory_kpis`) and the bands in `calibration/bands.json` leave blocking.
  - It is counted before any judging, so the results can't shrink it. A blocking KPI with
    no paired case still counts, as p = 1.
  - Flag-band KPIs count: they must SHIP, and their BLOCK goes to a human.
  - The PR comment prints K_gate under the gate table, with this paragraph.
- **N\* for a blocking KPI is sized at α/K_gate.** That is Holm's strictest step, the level
  the smallest p-value is tested at, so N* never understates the cases a BLOCK needs and can
  overstate them when other KPIs also reject. An advisory KPI's N* is sized at α. MDE stays
  at α for every KPI.
- **INCONCLUSIVE makes no claim.** It exits 0 and a human decides, unless
  `gate.inconclusive_blocks: true` (or `--fail-on-inconclusive`) makes it exit 1. The PR
  comment shows it as "No decision: needs_human"; [verdict-actions.md](verdict-actions.md)
  has what each outcome asks of the reviewer.
- **`fail-on` (T262).** Default `root-drift` fails on any named root (behaviour change).
  `harmed-root` fails only when a root is harmed by a blocking KPI or code check that
  **observes** that node's output or something downstream; unmeasured roots (no observing
  covering check — including same-node `node_visited` / `node_not_visited`, or a check
  scoped only to upstream nodes) are needs_human, never a pass; roots with observing
  checks that ran and did not fail (`no harm seen`) do not fail under that mode. The PR
  comment names why each unmeasured root is unmeasured.

  | Check kind | Measures a taxonomy root? |
  |---|---|
  | LLM / **jev** judge on that node | Yes (rubric over cassette output) |
  | `judge: fake` (constant label) | **No** (T264; never observes) |
  | `output_contains` / `output_matches` on that node | Yes (reads node output) |
  | `tool_called` / `tool_not_called` on that node | Yes (that node's tool calls; T210) |
  | `tool_sequence` / `max_steps` | Yes (run-level effects) |
  | `node_visited` / `node_not_visited` on a **downstream** node | Yes |
  | `node_sequence` that includes a node **after** the root (incl. `sub.inner`) | Yes |
  | `node_visited` / `node_not_visited` on the root itself | **No** (presence only) |
  | Check scoped only to nodes **before** the root on the run path | **No** |
  | Check skipped by `when:` for every sample | **Unmeasured** for that root (not "no harm seen") |

  **T264 examples** (how YAML maps under #230 harm status):

  ```yaml
  # 1) output_contains / output_matches — measured when the check runs
  - name: respond_shipped
    node: respond
    judge: code
    check: {type: output_contains, text: shipped}
    # or: check: {type: output_matches, pattern: "Status:\\s+SHIPPED"}  # pattern from base-ref
  # → root `respond` is measured (pass → no harm seen; fail → harmed)

  # 2) when: — skipped samples do not measure
  - name: order_shipped
    node: respond
    judge: code
    check:
      type: output_contains
      text: shipped
      when: {input_contains: order}
  # → input without "order": check skipped → that case contributes nothing
  # → if every sample skips, root stays unmeasured (never "no harm seen")
  # → if any sample runs the check, root is measured from those samples only

  # 3) unit path sub.inner — trajectory checks via with_units
  - name: inner_ran
    node: before
    judge: code
    check: {type: node_sequence, nodes: "before,sub.inner,after"}
  # → measures when the sequence includes a node after the root (incl. sub.inner)
  ```
- **A code check's new failure blocks outside these numbers.** A check that passed on the base
  and fails on the PR exits 1 on one case, whatever the paired test says. So it has no false-alarm
  bound: a check whose path varies between runs of an unchanged agent will block by chance, on
  63% of PRs with 100 live runs if it varies 1% of the time. List such a check in
  `gate.advisory_kpis`, and it reports the drop instead.
  - A case that also failed on the base isn't new, and a run replay stopped early isn't scored.
  - A PR that edits its own cassettes moves what they're compared with, so blocking code checks
    also run against the runs recorded on the base, paired by input. One that passes there and
    fails on the PR exits 1, named as hidden by the re-baseline. Otherwise the comment says no
    decision and it exits 0, so an intended change can land. A run the PR removes from its
    cassettes has nothing to compare against, so the comment names it, and it doesn't block.
  - `tool_called` and `tool_not_called` see only the tools the KPI's `node:` called (T210), read
    from that node's AI messages (including unit paths like `sub.inner`). A tool another node
    called does not pass or fail the check. If the named node was not recorded (e.g. it only ran
    inside a subgraph), the check is unmeasured for that case — never a silent pass. The comment
    no longer warns "whole-run" for these checks.
  - `tool_sequence` still reads the whole run; the comment says so. `node_sequence` and
    `tool_sequence`: a name that is in neither the base's recorded runs nor the PR's graph (a
    typo, or a node inside a subgraph, which lgcov doesn't record yet) leaves the check not
    checked, with a ⚠️ line, and never fails the PR. A name the base's runs have and the PR
    dropped is judged, so it fails.
- **The 2.5%** is at the default α = 0.05. With no discordant pair, SHIP reads the no-drop
  bound at α/2 instead of the CI. That is the same 2.5% at α = 0.05, and more if α is set
  higher, as the PR comment then says. Simulated with a BCa interval, it held from N = 200
  cases up: at N = 50 a drop of δ SHIPped 3.7% to 6.1% of the time. So binary KPIs read
  Tango's score interval, which stayed within 2.5% at every N simulated, 50 to 800. Graded KPIs
  still read BCa, and below N = 200 they aren't simulation-checked: there the one graded model
  simulated SHIPped a drop of δ up to 4.4% of the time. When half of a PR's failing results
  went missing, a drop of δ SHIPped up to 99.6% of the time
  ([gate simulations](validation/gate-simulations.md), [the fixes](design/gate-false-ship.md)).
- **A lost result leaves its case out.** A case both sides ran that only one side scored, as
  when the judge hit an infra error, isn't paired, so Δ doesn't see it. The PR comment counts
  these per side under the KPI's row. If the PR's failures are the ones the judge fails on, Δ
  reads too high. So when the PR side lost more results than the base by more than half the
  margin's worth of cases (at least 2), the KPI can't SHIP: it is INCONCLUSIVE and a human
  decides. Simulated, that kept a drop of δ from SHIPping more than 3.8% of the time when half
  of a PR's failing results went missing, against up to 99.7% without it. It's an interim rule
  ([the fixes](design/gate-false-ship.md)).
- **On a PR, these settings come from the base commit,** so a PR can't change what grades it. What
  that does and doesn't stop is in [the trust boundary](trust-boundary.md).
