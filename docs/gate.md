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
  - `tool_called` and `tool_not_called` see the tools a run called, read from the AI messages in
    its steps, but over the whole run: `node:` isn't applied to them yet, so a tool another node
    calls passes or fails the check too. The comment says so for each tool check.
  - `node_sequence` and `tool_sequence` read the whole run too. A name that is in neither the
    base's recorded runs nor the PR's graph (a typo, or a node inside a subgraph, which lgcov
    doesn't record yet) leaves the check not checked, with a ⚠️ line, and never fails the PR. A
    name the base's runs have and the PR dropped is judged, so it fails.
- **The 2.5%** is at the default α = 0.05. With no discordant pair, SHIP reads the no-drop
  bound at α/2 instead of the CI. That is the same 2.5% at α = 0.05, and more if α is set
  higher, as the PR comment then says.
- **On a PR, these settings come from the base commit,** so a PR can't change what grades it. What
  that does and doesn't stop is in [the trust boundary](trust-boundary.md).
