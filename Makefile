.PHONY: demo-baseline

# T079: re-record the demo agent baseline cassettes.
demo-baseline:
	uv run python -m examples.demo_agent.record
