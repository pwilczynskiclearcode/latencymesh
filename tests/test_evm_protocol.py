"""End-to-end: simulated network + NodeRegistry, honest and adversarial joins."""

import dataclasses
import hashlib
import itertools
import random
import statistics

import pytest

pytest.importorskip("boa")

from latencymesh.evm.chain import (
    STATUS_ACTIVE,
    STATUS_REJECTED,
    WEIGHT_NODE_UNIFORM,
    WEIGHT_REGION_UNIFORM,
    MeshChain,
)
from latencymesh.evm.protocol import run_join_round
from latencymesh.geoloc import calibrate
from latencymesh.model import Location
from latencymesh.sim import Simulation
from latencymesh.synth import WorldConfig, generate_world

pytestmark = pytest.mark.evm

WARSAW = Location(52.23, 21.01, "PL", "Warsaw", asn=65001)
TOKYO = Location(35.68, 139.69, "JP", "Tokyo", asn=65002)


@pytest.fixture(scope="module")
def world():  # type: ignore[no-untyped-def]
    return generate_world(WorldConfig(n_nodes=72, n_trusted=72, seed=8))


@pytest.fixture
def net(world):  # type: ignore[no-untyped-def]
    w = dataclasses.replace(world)  # shallow copy: tests add their own nodes
    w.nodes = dict(world.nodes)
    w.true_location = dict(world.true_location)
    w.access_ms = dict(world.access_ms)
    w.added_delay_ms = {}
    genesis = w.genesis()
    sim = Simulation(world=w, graph=genesis.to_graph(), seed=2)
    chain = MeshChain()
    regions = chain.register_genesis(genesis)
    return w, sim, chain, regions, calibrate(genesis)


def closest_pair_km(world, nodes) -> float:  # type: ignore[no-untyped-def]
    locs = [world.true_location[n] for n in nodes]
    return min(a.distance_km(b) for a, b in itertools.combinations(locs, 2))


def test_regions_follow_geography(net) -> None:  # type: ignore[no-untyped-def]
    world, _, _, regions, _ = net
    by_region: dict[int, set[str]] = {}
    for n, r in regions.items():
        by_region.setdefault(r, set()).add(world.true_location[n].continent)
    assert len(by_region) >= 8  # dozens of hubs collapse into a handful of regions
    assert sum(len(c) == 1 for c in by_region.values()) / len(by_region) > 0.8


def test_contract_draws_are_geographically_diverse(net) -> None:  # type: ignore[no-untyped-def]
    world, _, chain, regions, _ = net
    rng = random.Random(0)
    drawn, uniform = [], []
    for i in range(40):
        ws = chain.preview_witnesses(hashlib.sha256(bytes([i])).digest(), 8)
        assert len({regions[w] for w in ws}) == len(ws)  # one per region
        drawn.append(closest_pair_km(world, ws))
        uniform.append(closest_pair_km(world, rng.sample(list(regions), 8)))
    # no two contract-drawn witnesses are neighbours; plain random draws often are
    assert min(drawn) > 300
    assert sum(d < 300 for d in uniform) >= 10
    assert statistics.median(drawn) > 4 * statistics.median(uniform)


def test_flooding_one_region_does_not_capture_the_draw(net) -> None:  # type: ignore[no-untyped-def]
    world, sim, chain, regions, _ = net
    chain.set_params(weight_mode=WEIGHT_REGION_UNIFORM)
    big = max(set(regions.values()), key=lambda r: list(regions.values()).count(r))
    anchor = next(n for n, r in regions.items() if r == big)
    home = world.true_location[anchor]
    for i in range(20):  # an operator adds 20 honest-looking nodes in one region
        world.add_node(f"sybil{i}", home, 0.2)
        assert run_join_round(sim, chain, f"sybil{i}", anchor=anchor).accepted
    assert chain.registry.region_size(big) >= 20
    for i in range(30):
        ws = chain.preview_witnesses(hashlib.sha256(b"f" + bytes([i])).digest(), 8)
        assert sum(w.startswith("sybil") or regions.get(w) == big for w in ws) <= 1


def test_weight_mode_trades_diversity_for_per_node_fairness(net) -> None:  # type: ignore[no-untyped-def]
    _, _, chain, regions, _ = net
    sizes = {r: list(regions.values()).count(r) for r in set(regions.values())}
    big = max(sizes, key=lambda r: sizes[r])

    def share_of_big(mode: int) -> float:
        chain.set_params(weight_mode=mode)
        hits = 0
        for i in range(60):
            ws = chain.preview_witnesses(hashlib.sha256(b"w" + bytes([i])).digest(), 1)
            hits += regions[ws[0]] == big
        return hits / 60

    assert share_of_big(WEIGHT_NODE_UNIFORM) > share_of_big(WEIGHT_REGION_UNIFORM)


def test_honest_node_is_admitted(net) -> None:  # type: ignore[no-untyped-def]
    world, sim, chain, regions, lms = net
    world.add_node("honest", WARSAW, 0.3)
    out = run_join_round(sim, chain, "honest", landmarks=lms)
    assert out.accepted, out
    assert chain.status("honest") == STATUS_ACTIVE
    assert out.region == regions[out.anchor]
    assert world.true_location[out.anchor].continent == "EU"
    assert not out.disagreeing and not out.penalties


def test_newcomer_lying_about_rtts_is_rejected(net) -> None:  # type: ignore[no-untyped-def]
    world, sim, chain, _, lms = net
    world.add_node("liar", WARSAW, 0.3)
    sim.reporters["liar"] = lambda m: dataclasses.replace(m, rtt_ms=m.rtt_ms * 0.5)
    out = run_join_round(sim, chain, "liar", landmarks=lms)
    assert not out.accepted and chain.status("liar") == STATUS_REJECTED
    assert set(out.penalties) == {"liar"}


def test_false_location_claim_is_vetoed(net) -> None:  # type: ignore[no-untyped-def]
    world, sim, chain, _, lms = net
    world.add_node("fake-tokyo", WARSAW, 0.3, claimed=TOKYO)
    out = run_join_round(sim, chain, "fake-tokyo", landmarks=lms)
    assert out.vetoed and out.claim is not None and out.claim.too_fast


def test_delay_attack_cannot_fake_a_distant_location(net) -> None:  # type: ignore[no-untyped-def]
    world, sim, chain, _, lms = net
    world.add_node("delayer", WARSAW, 0.3, claimed=TOKYO)
    world.added_delay_ms["delayer"] = 45.0
    out = run_join_round(sim, chain, "delayer", landmarks=lms)
    assert out.vetoed and out.claim is not None and out.claim.too_slow


def test_lying_member_witness_is_penalised(net) -> None:  # type: ignore[no-untyped-def]
    world, sim, chain, _, _ = net
    chain.set_params(witness_count=12, weight_mode=WEIGHT_NODE_UNIFORM)
    for i in range(12):  # members that later act as witnesses
        world.add_node(f"m{i}", world.true_location[f"n{i:05d}"], 0.3)
        assert run_join_round(sim, chain, f"m{i}").accepted
    for i in range(12):
        sim.reporters[f"m{i}"] = lambda m: dataclasses.replace(m, rtt_ms=m.rtt_ms * 3)
    for j in range(20):
        world.add_node(f"new{j}", WARSAW, 0.3)
        out = run_join_round(sim, chain, f"new{j}")
        liars = [w for w in out.witnesses if w.startswith("m")]
        if liars and len(liars) / (len(out.witnesses) + 1) < 0.2:
            break
    else:
        pytest.fail("no round with a lying minority of member witnesses")
    assert out.accepted
    assert set(out.penalties) == set(liars)
    assert all(chain.accordance(m) < 1.0 for m in liars)


def test_honest_member_next_to_a_liar_is_not_penalised(net) -> None:  # type: ignore[no-untyped-def]
    """Regression: a co-located honest witness 'agrees' with a small lie (below the
    absolute tolerance) while the majority rejects the liar; it must not lose stake."""
    world, sim, chain, _, _ = net
    chain.set_params(witness_count=12, weight_mode=WEIGHT_NODE_UNIFORM)
    for i in range(6):
        world.add_node(f"waw{i}", WARSAW, 0.3)
        assert run_join_round(sim, chain, f"waw{i}").accepted
    for j in range(30):
        world.add_node(f"liar{j}", WARSAW, 0.3)
        sim.reporters[f"liar{j}"] = lambda m: dataclasses.replace(m, rtt_ms=m.rtt_ms * 0.5)
        out = run_join_round(sim, chain, f"liar{j}")
        neighbours = [w for w in out.witnesses if w.startswith("waw")]
        if neighbours:
            break
    else:
        pytest.fail("no co-located member drawn as witness")
    assert not out.accepted
    assert set(out.penalties) == {f"liar{j}"}
    assert all(chain.stake(w) == chain.min_stake for w in neighbours)
