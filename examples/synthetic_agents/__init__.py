"""T121: the agents the synthetic suite edits. Offline: fake models or none."""

from examples.demo_agent import mutations as demo
from examples.synthetic_agents import fanout, subgraph
from lgcov.synthetic import Agent

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
)
FANOUT = Agent("fanout", fanout.graph, QUESTIONS, fanout.MUTATIONS, fanout.outcome_ok)
SUBGRAPH = Agent("subgraph", subgraph.graph, QUESTIONS, subgraph.MUTATIONS, subgraph.outcome_ok)
AGENTS = (DEMO, FANOUT, SUBGRAPH)
