import random
import statistics

import pytest

from latencymesh.cluster import Level, average_linkage, cluster_hierarchy, pair_score
from latencymesh.geo import KM_PER_MS_RTT
from latencymesh.geoloc import calibrate, fit_bestline, locate, verify_claim
from latencymesh.model import Location
from latencymesh.synth import WorldConfig, generate_world


@pytest.fixture(scope="module")
def world():  # type: ignore[no-untyped-def]
    return generate_world(WorldConfig(n_nodes=200, n_trusted=72, seed=31, edge_fraction=0.0))


@pytest.fixture(scope="module")
def landmarks(world):  # type: ignore[no-untyped-def]
    return calibrate(world.genesis())


def test_average_linkage_merges_only_within_threshold() -> None:
    import numpy as np

    d = np.array([[0, 1, 9, 9], [1, 0, 9, 9], [9, 9, 0, 2], [9, 9, 2, 0]], dtype=float)
    assert sorted(average_linkage(d, 1.5)) == [[0, 1], [2], [3]]
    assert sorted(average_linkage(d, 2.5)) == [[0, 1], [2, 3]]
    assert len(average_linkage(d, 10)) == 1


def test_hierarchy_recovers_datacenters_cities_and_continents(world) -> None:  # type: ignore[no-untyped-def]
    nodes = list(world.nodes)
    rng = random.Random(0)

    def measured(a: str, b: str) -> float:
        return world.ping(a, b, rng, count=5)

    c = cluster_hierarchy(nodes, measured)
    loc = world.true_location
    dc = pair_score(c, "datacenter", lambda n: loc[n].datacenter)
    city = pair_score(c, "city", lambda n: loc[n].city)
    cont = pair_score(c, "continent", lambda n: loc[n].continent)
    assert dc.precision > 0.95 and dc.recall > 0.9
    assert city.precision > 0.95 and city.recall > 0.95
    assert cont.precision > 0.9 and cont.recall > 0.8
    some = nodes[0]
    assert c.path(some).startswith("continent-")


def test_levels_must_be_ordered(world) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        cluster_hierarchy(list(world.nodes)[:3], lambda a, b: 1.0, [Level("a", 5), Level("b", 1)])


def test_bestline_is_below_all_points_and_physical() -> None:
    d = [100, 500, 1000, 3000]
    r = [3.0, 8.0, 14.0, 40.0]
    bl = fit_bestline(d, r)
    assert bl.slope >= 1 / KM_PER_MS_RTT
    assert all(rt >= bl.slope * dk + bl.intercept - 1e-9 for dk, rt in zip(d, r, strict=True))


def test_cbg_locates_honest_nodes(world, landmarks) -> None:  # type: ignore[no-untyped-def]
    rng = random.Random(1)
    errors = []
    for nid in [n for n in world.nodes if not world.nodes[n].trusted][:25]:
        rtts = {lm: world.ping(nid, lm, rng) for lm in landmarks}
        est = locate(rtts, landmarks)
        assert est.feasible  # the speed-of-light region always contains an honest node
        errors.append(Location(est.lat, est.lon).distance_km(world.true_location[nid]))
    assert statistics.median(errors) < 200


def test_claim_verification_honest_vs_liars(world, landmarks) -> None:  # type: ignore[no-untyped-def]
    rng = random.Random(2)
    warsaw = Location(52.23, 21.01)
    tokyo = Location(35.68, 139.69)
    world.add_node("honest", warsaw, 0.3, claimed=warsaw)
    world.add_node("liar", warsaw, 0.3, claimed=tokyo)
    world.add_node("delayer", warsaw, 0.3, claimed=tokyo)
    world.added_delay_ms["delayer"] = 45.0  # tries to look far from Europe

    def check(nid: str) -> object:
        rtts = {lm: world.ping(nid, lm, rng) for lm in landmarks}
        claimed = world.nodes[nid].location
        assert claimed is not None
        return verify_claim(claimed, rtts, landmarks)

    honest, liar, delayer = check("honest"), check("liar"), check("delayer")
    assert honest.ok  # type: ignore[attr-defined]
    assert not liar.ok and liar.too_fast  # type: ignore[attr-defined]
    # uniform delay hides the speed-of-light violation, but Tokyo landmarks are far too slow
    assert not delayer.ok and not delayer.too_fast and delayer.too_slow  # type: ignore[attr-defined]
