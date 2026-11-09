from pathlib import Path

import pytest

from latencymesh.genesis import Genesis, GenesisError
from latencymesh.model import Continent, Location, Node

WAW = Location(52.23, 21.01, "PL", "Warsaw", Continent.EUROPE, asn=64500)
FRA = Location(50.11, 8.68, "DE", "Frankfurt", Continent.EUROPE, asn=64501)
NYC = Location(40.71, -74.01, "US", "New York", Continent.NORTH_AMERICA, asn=64502)


def nodes() -> list[Node]:
    return [Node("waw", True, WAW), Node("fra", True, FRA), Node("nyc", True, NYC)]


def test_roundtrip(tmp_path: Path) -> None:
    g = Genesis.build(nodes(), {("waw", "fra"): 18.0, ("nyc", "fra"): 80.0, ("waw", "nyc"): 95.0})
    p = tmp_path / "genesis.json"
    g.save(p)
    loaded = Genesis.load(p)
    assert loaded.rtt == g.rtt
    assert loaded.nodes[0].location == WAW
    graph = loaded.to_graph()
    assert graph.rtt("fra", "nyc") == 80.0
    assert sorted(graph.trusted_ids()) == ["fra", "nyc", "waw"]


def test_rejects_faster_than_light_and_missing_pairs() -> None:
    with pytest.raises(GenesisError) as e:
        Genesis.build(nodes(), {("waw", "fra"): 18.0, ("waw", "nyc"): 30.0})
    msgs = "\n".join(e.value.problems)
    assert "speed-of-light" in msgs
    assert "missing RTT" in msgs


def test_rejects_untrusted_nodes() -> None:
    with pytest.raises(GenesisError):
        Genesis.build([Node("x", False, WAW), Node("waw", True, WAW)], {("x", "waw"): 1.0})
