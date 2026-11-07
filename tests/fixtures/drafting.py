"""T106: a scripted drafting client and the demo agent's traces, for `taxonomy draft` tests."""

from lgcov.draft import DraftTrace, Failure, Proposal, TraceGroup
from lgcov.trace import RunTrace

TEAM = "A support agent: it classifies a question, looks orders up, and responds."
PASS = RunTrace(("classify", "lookup_order", "respond"), ("lookup_order",))
SKIPPED = RunTrace(("classify", "respond"))
TRACES = (
    DraftTrace("t1", PASS),
    DraftTrace("t2", PASS),
    DraftTrace("t3", SKIPPED, Failure("respond", "skipped_lookup", "no order status")),
    DraftTrace("t4", SKIPPED, Failure("respond", "skipped_lookup")),
    DraftTrace("t5", PASS, Failure("respond", "rude_tone", "told the customer to calm down")),
)
PROPOSALS = {
    "respond: skipped_lookup": Proposal(
        mode_id="skips_order_lookup",
        description="respond answers an order question without calling lookup_order.",
        why="Two order questions got an answer with no order status in it.",
        kpi_name="looks_up_the_order",
        kpi_description="Order questions go through lookup_order.",
        rubric="Did the run call lookup_order before answering?",
    ),
    "respond: rude_tone": Proposal(
        mode_id="rude_reply",
        description="respond's answer is curt or dismissive.",
        why="One reply told the customer to calm down.",
        kpi_name="reply_is_polite",
        kpi_description="The answer is polite.",
        rubric="Is the reply polite to the customer?",
    ),
    "classify: wrong_intent": Proposal(
        mode_id="misreads_intent",
        description="classify takes an order question for something else.",
        why="An order question was answered as small talk.",
        kpi_name="reads_order_intent",
        kpi_description="Order questions are classified as needing the tool.",
        rubric="Did classify mark the order question as needing lookup_order?",
    ),
}


class Scripted:
    """The fake LLM: one scripted proposal per failure signature; it records what it was
    asked, in order."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def propose(self, description: str, group: TraceGroup) -> Proposal:
        self.asked.append(group.signature)
        return PROPOSALS[group.signature]
