import statistics

import numpy as np
import pytest

from latencymesh.coords import GNP, Coordinate, Vivaldi, relative_errors
from latencymesh.sim import Simulation
from latencymesh.synth import WorldConfig, generate_world


@pytest.fixture(scope="module")
def world():  # type: ignore[no-untyped-def]
    return generate_world(WorldConfig(n_nodes=150, n_trusted=40, seed=5))


@pytest.fixture(scope="module")
def gnp(world):  # type: ignore[no-untyped-def]
    g = world.genesis()
    ids = [n.id for n in g.nodes]
    model = GNP(dims=5, seed=1)
    model.fit_landmarks(ids, g.to_graph().to_matrix(ids))
    return model


def test_coordinate_distance_includes_heights() -> None:
    a = Coordinate(np.array([0.0, 0.0]), 1.0)
    b = Coordinate(np.array([3.0, 4.0]), 2.0)
    assert a.distance(b) == pytest.approx(8.0)


def test_gnp_embeds_landmarks_accurately(world, gnp) -> None:  # type: ignore[no-untyped-def]
    ids = list(gnp.landmarks)
    pairs = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1 :]]
    errs = relative_errors(
        lambda a, b: gnp.landmarks[a].distance(gnp.landmarks[b]), world.base_rtt, pairs
    )
    assert statistics.median(errs) < 0.15


def test_gnp_positions_new_hosts(world, gnp) -> None:  # type: ignore[no-untyped-def]
    import random

    rng = random.Random(0)
    landmarks = list(gnp.landmarks)
    hosts = [n for n in world.nodes if n not in gnp.landmarks][:30]
    coords = {
        h: gnp.position({lm: world.ping(h, lm, rng) for lm in rng.sample(landmarks, 15)})
        for h in hosts
    }
    pairs = [(a, b) for i, a in enumerate(hosts) for b in hosts[i + 1 :]]
    errs = relative_errors(lambda a, b: coords[a].distance(coords[b]), world.base_rtt, pairs)
    assert statistics.median(errs) < 0.3


def test_gnp_rejects_too_few_landmarks(gnp) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        gnp.position({next(iter(gnp.landmarks)): 10.0})


def test_vivaldi_converges_in_simulation(world, gnp) -> None:  # type: ignore[no-untyped-def]
    viv = Vivaldi(dims=5, seed=2)
    for lid, c in gnp.landmarks.items():
        viv.anchor(lid, c)
    sim = Simulation.from_genesis(world, seed=3, probe_budget=4)
    sim.on_measurement(viv.observe)
    sim.run(60 * 40)
    hosts = [n for n in world.nodes if n not in gnp.landmarks]
    pairs = [(a, b) for i, a in enumerate(hosts[:60]) for b in hosts[i + 1 : 60]]
    errs = relative_errors(viv.predict, world.base_rtt, pairs)
    assert len(errs) == len(pairs)
    assert statistics.median(errs) < 0.3
    assert viv.mean_error() < 0.5
    # anchors never move
    some = next(iter(gnp.landmarks))
    assert np.allclose(viv.coordinate(some).vec, gnp.landmarks[some].vec)


def test_vivaldi_unknown_nodes_have_no_prediction() -> None:
    viv = Vivaldi()
    assert viv.predict("a", "b") is None
    viv.update("a", "b", 10.0)
    assert viv.predict("a", "b") is None  # b never updated nor anchored
