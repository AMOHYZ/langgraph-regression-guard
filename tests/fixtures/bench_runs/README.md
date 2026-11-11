# Saved bench runs (T162)

`lgcov bench --from-runs` reads `<benchmark>/{base,head}.json`, the layout `--keep-runs` saves.

`open_deep_research-163/` is the #163 pair stage 1 kept (`lgcov-bench/stage1/runs`, 5 Nov 2026,
one case). Each step's input, output and goto is replaced by the first 16 hex digits of the
SHA-256 of its normalised JSON (`lgcov.normalize.normalize`, sorted keys), and the run's inputs by
`{}`. Node names and code fingerprints are kept. So the pair drifts exactly where the real one does
(`final_report_generation`, `research_supervisor`, `write_research_brief`) without carrying the
model's text, and scoring it makes no call.
