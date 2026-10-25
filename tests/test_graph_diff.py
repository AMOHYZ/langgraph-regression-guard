from lgcov.graph_diff import Edge, GraphSnapshot, diff_graphs


def _snap(nodes: set[str], edges: set[tuple[str, str]]) -> GraphSnapshot:
    return GraphSnapshot(nodes=frozenset(nodes), edges=frozenset(Edge(a, b) for a, b in edges))


def test_identical_graphs_have_empty_diff() -> None:
    g = _snap({"agent", "tools"}, {("agent", "tools")})
    assert diff_graphs(g, g).is_empty


def test_added_node_and_edge_are_reported() -> None:
    old = _snap({"agent", "tools"}, {("agent", "tools")})
    new = _snap({"agent", "tools", "reviewer"}, {("agent", "tools"), ("tools", "reviewer")})

    diff = diff_graphs(old, new)

    assert diff.added_nodes == {"reviewer"}
    assert diff.added_edges == {Edge("tools", "reviewer")}
    assert not diff.removed_nodes
    assert not diff.removed_edges


def test_removed_node_and_edge_are_reported() -> None:
    old = _snap({"agent", "tools"}, {("agent", "tools")})
    new = _snap({"agent"}, set())

    diff = diff_graphs(old, new)

    assert diff.removed_nodes == {"tools"}
    assert diff.removed_edges == {Edge("agent", "tools")}
