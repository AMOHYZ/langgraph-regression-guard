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
- **INCONCLUSIVE makes no claim.** It exits 0 and a human decides, unless
  `gate.inconclusive_blocks: true` (or `--fail-on-inconclusive`) makes it exit 1.
- **The 2.5%** is at the default α = 0.05. With no discordant pair, SHIP reads the no-drop
  bound at α/2 instead of the CI. That is the same 2.5% at α = 0.05, and more if α is set
  higher, as the PR comment then says.
