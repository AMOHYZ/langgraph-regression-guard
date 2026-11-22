"""T121: the agents the synthetic suite edits. Offline: fake models or none."""

from examples.demo_agent import mutations as demo
from examples.synthetic_agents import fanout, subgraph, support
from lgcov.synthetic import Agent, SharedAgent

# Two order questions, which give each edit the same label today, and one that isn't about an
# order. `outcome_ok` limits them: a long order question fails on base, so it can't be used.
QUESTIONS = ("Where is my order?", "Order status?", "What are your hours?")

DEMO = Agent(
    "demo",
    demo.build(),  # the agent as recorded: the demo PR edits graph.py's prompt
    QUESTIONS,
    {
        operator: demo.MUTATIONS.get(operator, ()) + demo.MORE_MUTATIONS.get(operator, ())
        for operator in demo.MUTATIONS
    },
    demo.outcome_ok,
    demo.noisy_graph,
    demo.SHIFTS,
)
FANOUT = Agent("fanout", fanout.graph, QUESTIONS, fanout.MUTATIONS, fanout.outcome_ok)
SUBGRAPH = Agent("subgraph", subgraph.graph, QUESTIONS, subgraph.MUTATIONS, subgraph.outcome_ok)
AGENTS = (DEMO, FANOUT, SUBGRAPH)

# T182: edits the diff can't pin to one node. Apart from AGENTS, so T121's suite stays as it is.
SUPPORT = SharedAgent("support", support.graph, QUESTIONS, support.EDITS, support.outcome_ok)
SUPPORT_NESTED = SharedAgent(
    "support-nested", support.nested_graph, QUESTIONS, support.NESTED_EDITS, support.outcome_ok
)
SUPPORT_CLIENT = SharedAgent(
    "support-client", support.client_graph, QUESTIONS, support.CLIENT_EDITS, support.outcome_ok
)
SHARED_AGENTS = (SUPPORT, SUPPORT_NESTED, SUPPORT_CLIENT)

# T264: held-out edits stay apart from AGENTS / every_case; import for the opt-in API.
from examples.synthetic_agents.held_out import HELD_OUT as HELD_OUT  # noqa: E402
from examples.synthetic_agents.held_out import (  # noqa: E402
    every_held_out_case as every_held_out_case,
)
