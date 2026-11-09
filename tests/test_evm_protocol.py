import dataclasses

import pytest

pytest.importorskip("boa")

from latencymesh.evm.chain import STATUS_ACTIVE, STATUS_REJECTED, MeshChain
from latencymesh.evm.protocol import Tolerance, run_join_round
from latencymesh.geoloc import calibrate
from latencymesh.model import Location
from latencymesh.select import Beacon, dispersed, make_distance
from latencymesh.sim import Simulation
from latencymesh.synth import WorldConfig, generate_world

pytestmark = pytest.mark.evm

WARSAW = Location(52.23, 21.01, "PL", "Warsaw", asn=65001)
TOKYO = Location(35.68, 139.69, "JP", "Tokyo", asn=65002)


@pytest.fixture
def net():  # type: ignore[no-untyped-def]
    world = generate_world(WorldConfig(n_nodes=60, n_trusted=40, seed=8))
    genesis = world.genesis()
    sim = Simulation(world=world, graph=genesis.to_graph(), seed=2)
    chain = MeshChain()
    chain.register_genesis(genesis)
    return world, sim, chain, calibrate(genesis)


def newcomer(world, sim, chain, node_id, true_loc, claimed):  # type: ignore[no-untyped-def]
    node = world.add_node(node_id, true_loc, 0.3, claimed=claimed)
    sim.join(node_id)
    chain.join(node)
    trusted = world.trusted_ids()
    # witnesses drawn with beacon randomness *after* the join, spread over the network
    rng = Beacon(seed=99).rng(node_id)
    return dispersed(trusted, 12, make_distance(graph=sim.graph), rng)


def test_honest_node_is_admitted(net) -> None:  # type: ignore[no-untyped-def]
    world, sim, chain, lms = net
    w = newcomer(world, sim, chain, "honest", WARSAW, WARSAW)
    out = run_join_round(sim, chain, "honest", w, landmarks=lms)
    assert out.accepted, out.reasons
    assert chain.status("honest") == STATUS_ACTIVE
    assert len(out.agreeing) == len(w)


def test_newcomer_lying_about_rtts_is_rejected(net) -> None:  # type: ignore[no-untyped-def]
    world, sim, chain, lms = net
    w = newcomer(world, sim, chain, "liar", WARSAW, WARSAW)
    sim.reporters["liar"] = lambda m: dataclasses.replace(m, rtt_ms=m.rtt_ms * 0.5)
    out = run_join_round(sim, chain, "liar", w, landmarks=lms)
    assert not out.accepted
    assert chain.status("liar") == STATUS_REJECTED
    assert any("witnesses agree" in r for r in out.reasons)


def test_false_location_claim_is_rejected(net) -> None:  # type: ignore[no-untyped-def]
    world, sim, chain, lms = net
    w = newcomer(world, sim, chain, "fake-tokyo", WARSAW, TOKYO)
    out = run_join_round(sim, chain, "fake-tokyo", w, landmarks=lms)
    assert not out.accepted and out.claim is not None and out.claim.too_fast


def test_delay_attack_cannot_fake_a_distant_location(net) -> None:  # type: ignore[no-untyped-def]
    world, sim, chain, lms = net
    w = newcomer(world, sim, chain, "delayer", WARSAW, TOKYO)
    world.added_delay_ms["delayer"] = 45.0
    out = run_join_round(sim, chain, "delayer", w, landmarks=lms)
    assert not out.accepted
    assert out.claim is not None and not out.claim.too_fast and out.claim.too_slow


def test_withheld_reveal_gets_newcomer_slashed(net) -> None:  # type: ignore[no-untyped-def]
    world, sim, chain, lms = net
    w = newcomer(world, sim, chain, "quitter", WARSAW, WARSAW)
    out = run_join_round(sim, chain, "quitter", w, landmarks=lms, withhold_reveal=["quitter"])
    assert not out.accepted and out.slashed == ["quitter"]
    assert chain.stake("quitter") == 0  # slashed before rejection could refund it


def test_tolerance_mixes_absolute_and_relative() -> None:
    tol = Tolerance(rel=0.2, abs_ms=1.5)
    assert tol.agree(1.0, 2.4)  # absolute floor for tiny RTTs
    assert not tol.agree(100.0, 125.0)
    assert tol.agree(100.0, 119.0)
