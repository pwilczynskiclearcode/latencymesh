"""Witness selection: the three lines around a target and the 4 chosen witnesses."""

from collections import Counter

import boa
import pytest

from latencymesh import Mesh, Network
from latencymesh.network import seed


def reference_lines(net: Network, target: str) -> tuple[list[str], list[str], list[str]]:
    """The definition of the lines, written independently of the contract."""
    nearest = {n: [name for name, _ in net.nearest_k(n)] for n in net.nodes}
    line1 = nearest[target]
    line2: list[str] = []
    for a in line1:
        line2 += [n for n in nearest[a] if n != target and n not in line1 and n not in line2]
    line3: list[str] = []
    for a in line2:
        line3 += [n for n in nearest[a] if n not in [target, *line1, *line2, *line3]]
    return line1, line2, line3


def test_lines_match_the_definition_for_every_node(mesh: Mesh, milky_way: Network) -> None:
    for target in milky_way.nodes:
        assert mesh.lines_of(target) == reference_lines(milky_way, target)


def test_lines_never_overlap_or_contain_the_target(mesh: Mesh, milky_way: Network) -> None:
    for target in milky_way.nodes:
        l1, l2, l3 = mesh.lines_of(target)
        everyone = [target, *l1, *l2, *l3]
        assert len(everyone) == len(set(everyone))
        assert len(l1) == 3 and len(l2) <= 9 and len(l3) <= 27


def test_example_lines_around_warsaw(mesh: Mesh) -> None:
    l1, l2, _ = mesh.lines_of("gcp-europe-central2")
    assert l1[0] == "azure-polandcentral"  # same city, other provider
    assert "gcp-europe-central2" not in l2 and not set(l1) & set(l2)


def test_four_distinct_witnesses_each_from_its_line(mesh: Mesh, milky_way: Network) -> None:
    for target in milky_way.nodes:
        lines = mesh.lines_of(target)
        for cycle in range(10):
            picked = mesh.witnesses_for(target, seed(cycle))
            names = [n for n, _ in picked]
            assert len(set(names)) == 4 and target not in names
            for position, (name, line) in enumerate(picked[:3], start=1):
                if line == position:
                    assert name in lines[position - 1]
                else:  # the line was empty, so the witness came from the whole network
                    assert line == 0 and not lines[position - 1]
            assert picked[3][1] == 0


def test_same_seed_same_witnesses_different_seed_different_witnesses(mesh: Mesh) -> None:
    target = "aws-us-east-1"
    assert mesh.witnesses_for(target, seed(1)) == mesh.witnesses_for(target, seed(1))
    draws = {tuple(mesh.witnesses_for(target, seed(i))) for i in range(30)}
    assert len(draws) > 20


def test_seed_cannot_be_reused_across_targets(mesh: Mesh) -> None:
    """Two targets with the same line 1 must not get the same pick from one seed."""
    same = 0
    for i in range(60):
        a = mesh.witnesses_for("aws-eu-central-1", seed(i))[3][0]
        b = mesh.witnesses_for("gcp-europe-west3", seed(i))[3][0]
        same += a == b
    assert same < 10  # global picks are independent per target


def test_each_line1_node_is_chosen_about_a_third_of_the_time(mesh: Mesh) -> None:
    target = "aws-ap-northeast-1"
    line1, _, _ = mesh.lines_of(target)
    counts = Counter(mesh.witnesses_for(target, seed(i))[0][0] for i in range(600))
    assert set(counts) == set(line1)
    assert all(150 < c < 250 for c in counts.values()), counts


def test_global_witness_reaches_the_whole_network(mesh: Mesh, milky_way: Network) -> None:
    target = "aws-eu-west-1"
    seen = {mesh.witnesses_for(target, seed(i))[3][0] for i in range(1500)}
    assert len(seen) >= len(milky_way.nodes) - 6  # all but the target and a few unlucky ones


def test_unknown_target_is_rejected(mesh: Mesh) -> None:
    with boa.reverts("unknown node"):
        mesh.contract.witnesses_for(boa.env.generate_address(), seed(0))
    with boa.reverts("unknown node"):
        mesh.contract.lines_of(boa.env.generate_address())


# --- tiny hand-made networks for edge cases -----------------------------------------


def tiny(links: dict[str, list[str]]):  # type: ignore[no-untyped-def]
    contract = boa.load("src/latencymesh/contracts/WitnessMesh.vy")
    addr = {name: str(boa.env.generate_address(alias=name)) for name in links}
    name = {a: n for n, a in addr.items()}
    for a in addr.values():
        contract.add_node(a)
    for node, neighbours in links.items():
        contract.set_nearest(addr[node], [addr[n] for n in neighbours], [1000, 2000, 3000])
    contract.seal()
    return contract, addr, name


def test_tight_cluster_has_no_line2_and_falls_back_to_the_network() -> None:
    # a, b, c, d are each other's nearest nodes; x, y, z hang off the cluster
    contract, addr, name = tiny(
        {
            "a": ["b", "c", "d"],
            "b": ["a", "c", "d"],
            "c": ["a", "b", "d"],
            "d": ["a", "b", "c"],
            "x": ["a", "b", "c"],
            "y": ["a", "b", "c"],
            "z": ["a", "b", "c"],
        }
    )
    l1, l2, l3 = contract.lines_of(addr["a"])
    assert [name[str(n)] for n in l1] == ["b", "c", "d"] and l2 == [] and l3 == []
    for i in range(50):
        chosen, origin = contract.witnesses_for(addr["a"], seed(i))
        names = [name[str(c)] for c in chosen]
        assert list(origin) == [1, 0, 0, 0]
        assert len(set(names)) == 4 and "a" not in names
        assert names[0] in ("b", "c", "d")


def test_smallest_possible_network_still_gives_four_witnesses() -> None:
    contract, addr, name = tiny(
        {
            "a": ["b", "c", "d"],
            "b": ["a", "c", "d"],
            "c": ["a", "b", "d"],
            "d": ["a", "b", "c"],
            "e": ["a", "b", "c"],
        }
    )
    for target in addr:
        for i in range(40):
            chosen, _ = contract.witnesses_for(addr[target], seed(i))
            names = {name[str(c)] for c in chosen}
            assert names == set(addr) - {target}  # the only 4 other nodes


@pytest.mark.parametrize("target", ["aws-af-south-1", "aws-sa-east-1", "aws-ap-southeast-2"])
def test_remote_nodes_also_get_local_witnesses(mesh: Mesh, target: str) -> None:
    """Even far from the dense regions, line 1 is the closest the network has."""
    l1, _, _ = mesh.lines_of(target)
    nearest_rtt = mesh.contract.nearest_of(mesh.address[target])[1]
    assert len(l1) == 3 and nearest_rtt[0] <= 30_000  # within 30 ms
