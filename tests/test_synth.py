import random
import statistics

import pytest

from latencymesh.geo import min_rtt_ms
from latencymesh.model import Location
from latencymesh.synth import WorldConfig, generate_world


@pytest.fixture(scope="module")
def world():  # type: ignore[no-untyped-def]
    return generate_world(WorldConfig(n_nodes=120, n_trusted=40, seed=7))


def test_deterministic_from_seed() -> None:
    a = generate_world(WorldConfig(n_nodes=30, n_trusted=10, seed=3))
    b = generate_world(WorldConfig(n_nodes=30, n_trusted=10, seed=3))
    assert a.true_location == b.true_location
    assert a.base_rtt("n00001", "n00020") == b.base_rtt("n00001", "n00020")


def test_rtt_is_symmetric_and_respects_physics(world) -> None:  # type: ignore[no-untyped-def]
    ids = list(world.nodes)
    for i in range(0, len(ids), 7):
        for j in range(1, len(ids), 11):
            a, b = ids[i], ids[j]
            if a == b:
                continue
            assert world.base_rtt(a, b) == world.base_rtt(b, a)
            dist = world.true_location[a].distance_km(world.true_location[b])
            assert world.base_rtt(a, b) >= min_rtt_ms(dist)


def test_trusted_nodes_cover_all_cities(world) -> None:  # type: ignore[no-untyped-def]
    cities = {world.nodes[i].location.city for i in world.trusted_ids()}
    assert len(cities) == len(world.config.cities)


def test_ping_burst_min_is_close_to_base(world) -> None:  # type: ignore[no-untyped-def]
    rng = random.Random(1)
    a, b = "n00000", "n00005"
    base = world.base_rtt(a, b)
    pings = [world.ping(a, b, rng, count=10) for _ in range(50)]
    assert min(pings) >= base
    assert statistics.median(pings) - base < 0.5


def test_tiv_fraction_roughly_matches_config(world) -> None:  # type: ignore[no-untyped-def]
    ids = list(world.nodes)
    pairs = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1 :]]
    share = sum(world.tiv_inflation(a, b) > 1 for a, b in pairs) / len(pairs)
    assert share == pytest.approx(world.config.tiv_fraction, abs=0.02)


def test_delay_attack_only_increases_rtt(world) -> None:  # type: ignore[no-untyped-def]
    world.add_node("evil", Location(52.2, 21.0), 0.2, claimed=Location(35.7, 139.7))
    rng = random.Random(2)
    honest = world.ping("evil", "n00000", rng)
    world.added_delay_ms["evil"] = 40.0
    delayed = world.ping("evil", "n00000", rng)
    assert delayed > honest + 70
    assert world.nodes["evil"].location.lat == pytest.approx(35.7)  # the claim


def test_genesis_from_world_validates(world) -> None:  # type: ignore[no-untyped-def]
    g = world.genesis()
    assert len(g.nodes) == 40
    assert len(g.rtt) == 40 * 39 // 2
