"""T079: record the demo agent's baseline cassettes (`make demo-baseline`).

Two inputs so both branches of the conditional edge are covered.
"""

import json
from dataclasses import asdict
from pathlib import Path

from examples.demo_agent.graph import graph
from lgcov.cassette import record

INPUTS = [
    {"question": "Where is my order?"},  # goes through lookup_order
    {"question": "What are your hours?"},  # skips the tool
]
BASELINE = Path(__file__).parent / "cassettes" / "baseline.json"


def baseline_json() -> str:
    runs = [asdict(record(graph, inputs)) for inputs in INPUTS]
    return json.dumps({"runs": runs}, indent=2, sort_keys=True) + "\n"


def main() -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(baseline_json(), encoding="utf-8")
    print(f"wrote {BASELINE}")


if __name__ == "__main__":
    main()
