"""The milky-way genesis: is the data sane, and does the chain hold exactly that data?"""

import boa
import pytest

from latencymesh import Mesh, Network, deploy
from latencymesh.network import K, ms_to_us


def test_milky_way_covers_three_providers(milky_way: Network) -> None:
    providers = {n.provider for n in milky_way.nodes.values()}
    assert providers == {"aws", "gcp", "azure"}
    assert len(milky_way.nodes) == 68
    assert len({n.country for n in milky_way.nodes.values()}) >= 25


def test_every_node_has_k_valid_nearest_nodes(milky_way: Network) -> None:
    for node_id in milky_way.nodes:
        ranked = milky_way.nearest_k(node_id)
        names = [n for n, _ in ranked]
        assert len(set(names)) == K and node_id not in names
        assert all(n in milky_way.nodes for n in names)
        assert all(rtt > 0 for _, rtt in ranked)


def test_chain_holds_the_genesis(mesh: Mesh, milky_way: Network) -> None:
    assert mesh.contract.sealed()
    assert mesh.contract.node_count() == len(milky_way.nodes)
    for node_id in milky_way.nodes:
        neighbours, rtt_us = mesh.contract.nearest_of(mesh.address[node_id])
        expected = milky_way.nearest_k(node_id)
        assert mesh.names(neighbours) == [n for n, _ in expected]
        assert list(rtt_us) == [ms_to_us(rtt) for _, rtt in expected]


def test_same_city_nodes_of_other_providers_are_first_neighbours(mesh: Mesh) -> None:
    line1, _, _ = mesh.lines_of("aws-eu-central-1")  # Frankfurt
    assert set(line1[:2]) == {"gcp-europe-west3", "azure-germanywestcentral"}


def test_sealed_genesis_cannot_be_changed(mesh: Mesh) -> None:
    a = mesh.address["aws-eu-central-1"]
    others = [mesh.address[n] for n in ("aws-eu-west-1", "aws-eu-west-2", "aws-eu-west-3")]
    with boa.reverts("genesis sealed"):
        mesh.contract.add_node(boa.env.generate_address())
    with boa.reverts("genesis sealed"):
        mesh.contract.set_nearest(a, others, [1, 2, 3])
    with boa.reverts("genesis sealed"):
        mesh.contract.seal()


def test_only_owner_can_build_the_genesis(milky_way: Network) -> None:
    mesh = deploy(milky_way, seal=False)
    a = mesh.address["aws-eu-central-1"]
    others = [mesh.address[n] for n in ("aws-eu-west-1", "aws-eu-west-2", "aws-eu-west-3")]
    with boa.env.prank(a):
        with boa.reverts("only owner"):
            mesh.contract.add_node(boa.env.generate_address())
        with boa.reverts("only owner"):
            mesh.contract.set_nearest(a, others, [1, 2, 3])
        with boa.reverts("only owner"):
            mesh.contract.seal()


@pytest.mark.parametrize(
    ("neighbours", "rtt", "error"),
    [
        (["self", "b", "c"], [1, 2, 3], "node cannot be its own neighbour"),
        (["b", "b", "c"], [1, 2, 3], "duplicate neighbour"),
        (["b", "c", "stranger"], [1, 2, 3], "unknown neighbour"),
        (["b", "c", "d"], [3, 2, 1], "neighbours must be sorted by rtt"),
        (["b", "c", "d"], [0, 2, 3], "rtt must be positive"),
    ],
)
def test_invalid_links_are_rejected(neighbours: list[str], rtt: list[int], error: str) -> None:
    contract = boa.load("src/latencymesh/contracts/WitnessMesh.vy")
    addr = {name: boa.env.generate_address() for name in ("self", "b", "c", "d", "stranger")}
    for name in ("self", "b", "c", "d"):
        contract.add_node(addr[name])
    with boa.reverts(error):
        contract.set_nearest(addr["self"], [addr[n] for n in neighbours], rtt)


def test_cannot_seal_an_incomplete_genesis() -> None:
    contract = boa.load("src/latencymesh/contracts/WitnessMesh.vy")
    nodes = [boa.env.generate_address() for _ in range(5)]
    for n in nodes[:4]:
        contract.add_node(n)
    with boa.reverts("need at least 5 nodes"):
        contract.seal()
    contract.add_node(nodes[4])
    with boa.reverts("a node has no links"):
        contract.seal()
    with boa.reverts("already a node"):
        contract.add_node(nodes[0])
    with boa.reverts("genesis not sealed"):
        contract.witnesses_for(nodes[0], b"\x00" * 32)
