.PHONY: demo-baseline bench-speed

# T079: re-record the demo agent baseline cassettes.
demo-baseline:
	uv run python -m examples.demo_agent.record

# T105: the demo PR through lgcov ci, 5x served and 5x --live; writes docs/benchmark-speed.md.
bench-speed:
	uv run python -m lgcov.bench --runs 5 --out docs/benchmark-speed.md
