import math

import numpy as np
import pytest

from latencymesh.graph import RTTGraph
from latencymesh.model import Location, Measurement, Node


def make_graph(window: int = 3) -> RTTGraph:
    g = RTTGraph(window=window)
    for i in "abcd":
        g.add_node(Node(i))
    return g


def test_edge_is_min_over_window_and_undirected() -> None:
    g = make_graph(window=3)
    for v in (12.0, 10.0, 15.0):
        g.record(Measurement("a", "b", v))
    assert g.rtt("a", "b") == 10.0
    assert g.rtt("b", "a") == 10.0
    # old minimum falls out of the window -> estimate follows routing change
    for v in (20.0, 21.0, 22.0):
        g.record(Measurement("a", "b", v))
    assert g.rtt("a", "b") == 20.0


def test_disagreement_between_observers() -> None:
    g = make_graph()
    g.record(Measurement("a", "b", 10.0))
    g.record(Measurement("b", "a", 12.5))
    edge = g.edge("a", "b")
    assert edge is not None
    assert edge.disagreement() == pytest.approx(0.2)
    assert edge.observer_rtt("b") == 12.5


def test_neighbors_matrix_and_removal() -> None:
    g = make_graph()
    g.record(Measurement("a", "b", 5.0))
    g.record(Measurement("a", "c", 7.0))
    assert g.nearest_measured("a", 1) == [("b", 5.0)]
    m = g.to_matrix(["a", "b", "c", "d"])
    assert m[0, 2] == 7.0 and math.isnan(m[0, 3]) and np.all(np.diag(m) == 0)
    g.remove_node("a")
    assert g.edge_count == 0 and "a" not in g


def test_measurement_validation() -> None:
    with pytest.raises(ValueError):
        Measurement("a", "a", 1.0)
    with pytest.raises(ValueError):
        Measurement("a", "b", float("nan"))
    with pytest.raises(ValueError):
        Location(91, 0)
