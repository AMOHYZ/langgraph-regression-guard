"""T079: record the demo agent's baseline cassettes (`make demo-baseline`).

Two inputs so both branches of the conditional edge are covered.
"""

from pathlib import Path

from examples.demo_agent.graph import graph
from lgcov.cassette import (
    SCHEME,
    Recording,
    attach_parts,
    chat_parts,
    graph_shape,
    record,
    recording_to_json,
)

INPUTS = [
    {"question": "Where is my order?"},  # goes through lookup_order
    {"question": "What are your hours?"},  # skips the tool
]
BASELINE = Path(__file__).parent / "cassettes" / "baseline.json"


def baseline_json() -> str:
    """Schema 2, the same bytes `lgcov record` writes for this graph."""
    runs = [record(graph, inputs, units=True) for inputs in INPUTS]
    parts_table, by_node = chat_parts(graph)
    if by_node:
        runs = [attach_parts(r, by_node) for r in runs]
    return recording_to_json(Recording(runs, 2, SCHEME, graph_shape(graph), parts_table or None))


def main() -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(baseline_json(), encoding="utf-8")
    print(f"wrote {BASELINE}")


if __name__ == "__main__":
    main()
