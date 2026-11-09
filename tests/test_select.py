import random
import statistics
from collections import Counter

import pytest

from latencymesh.coords import GNP, Coordinate
from latencymesh.select import (
    Beacon,
    EpochPermutation,
    SelectionLedger,
    _hilbert_d,
    around,
    balanced,
    dispersed,
    hilbert_order,
    hilbert_systematic,
    iterative_nearest,
    k_nearest,
    make_distance,
    mean_pairwise,
    swap_or_not_index,
    uniform,
)
from latencymesh.synth import WorldConfig, generate_world


@pytest.fixture(scope="module")
def setup():  # type: ignore[no-untyped-def]
    world = generate_world(WorldConfig(n_nodes=240, n_trusted=40, seed=21))
    g = world.genesis()
    ids = [n.id for n in g.nodes]
    gnp = GNP(dims=5, seed=0)
    gnp.fit_landmarks(ids, g.to_graph().to_matrix(ids))
    rng = random.Random(0)
    coords: dict[str, Coordinate] = dict(gnp.landmarks)
    for h in world.nodes:
        if h not in coords:
            coords[h] = gnp.position({lm: world.ping(h, lm, rng) for lm in rng.sample(ids, 15)})
    return world, coords, make_distance(coords=coords)


def test_hilbert_index_is_a_continuous_bijection() -> None:
    bits = 3
    side = 1 << bits
    cells = {_hilbert_d(bits, x, y): (x, y) for x in range(side) for y in range(side)}
    assert sorted(cells) == list(range(side * side))
    for d in range(side * side - 1):
        (x1, y1), (x2, y2) = cells[d], cells[d + 1]
        assert abs(x1 - x2) + abs(y1 - y2) == 1


def test_beacon_is_deterministic_per_round_and_label() -> None:
    b = Beacon(seed=1)
    assert b.value("x") == Beacon(seed=1).value("x")
    assert b.value("x") != b.value("y")
    v = b.value("x")
    b.next_round()
    assert b.value("x") != v


def test_balanced_selection_beats_uniform_on_fairness(setup) -> None:  # type: ignore[no-untyped-def]
    world, _, _ = setup
    nodes = list(world.nodes)
    beacon = Beacon(seed=3)
    led_u, led_b = SelectionLedger(), SelectionLedger()
    for _ in range(600):
        rng = beacon.rng("draw")
        led_u.record(uniform(nodes, 10, rng))
        balanced(nodes, 10, beacon.rng("balanced"), led_b)
        beacon.next_round()
    assert led_b.spread() < led_u.spread() / 2
    assert statistics.mean(led_b.counts.values()) == pytest.approx(25.0)


def test_ledger_newcomer_starts_at_mean() -> None:
    led = SelectionLedger()
    led.record(["a", "a", "b"])  # a=2, b=1
    led.ensure(["c"])
    assert led.counts["c"] == pytest.approx(1.5)


def test_hilbert_systematic_is_uniform_and_spread(setup) -> None:  # type: ignore[no-untyped-def]
    _, coords, dist = setup
    order = hilbert_order(coords)
    assert sorted(order) == sorted(coords)
    beacon = Beacon(seed=4)
    counts: Counter[str] = Counter()
    spread_h, spread_u = [], []
    for _ in range(400):
        rng = beacon.rng()
        s = hilbert_systematic(order, 12, rng)
        counts.update(s)
        spread_h.append(mean_pairwise(s, dist))
        spread_u.append(mean_pairwise(uniform(order, 12, rng), dist))
        beacon.next_round()
    expected = 400 * 12 / len(order)
    assert len(counts) > 0.95 * len(order)
    assert statistics.mean(counts.values()) == pytest.approx(expected, rel=0.1)
    assert statistics.mean(spread_h) > statistics.mean(spread_u)


def test_dispersed_maximises_spread(setup) -> None:  # type: ignore[no-untyped-def]
    world, _, dist = setup
    nodes = list(world.nodes)
    rng = random.Random(5)
    d = [mean_pairwise(dispersed(nodes, 10, dist, rng), dist) for _ in range(20)]
    u = [mean_pairwise(uniform(nodes, 10, rng), dist) for _ in range(20)]
    assert statistics.mean(d) > 1.3 * statistics.mean(u)


def test_around_and_k_nearest(setup) -> None:  # type: ignore[no-untyped-def]
    world, _, dist = setup
    nodes = list(world.nodes)
    center = nodes[0]
    near = k_nearest(center, nodes, 5, dist)
    assert [d for _, d in near] == sorted(d for _, d in near)
    picked = around(center, nodes, 5, dist, random.Random(1), radius_ms=30.0)
    assert all((dist(center, p) or 1e9) <= 30.0 for p in picked)
    pool = {c for c, _ in k_nearest(center, nodes, 20, dist)}
    assert set(around(center, nodes, 5, dist, random.Random(2))) <= pool


def test_iterative_nearest_finds_true_neighbours_cheaply(setup) -> None:  # type: ignore[no-untyped-def]
    world, coords, dist = setup
    existing = list(world.nodes)
    rng = random.Random(7)
    recalls, probes = [], []
    for newcomer in rng.sample(existing, 15):
        others = [n for n in existing if n != newcomer]
        truth = sorted(others, key=lambda o: world.base_rtt(newcomer, o))[:3]
        res = iterative_nearest(
            lambda c, nc=newcomer: world.ping(nc, c, rng),
            others,
            dist,
            rng,
            x=10,
            t=3,
            coords={k: v for k, v in coords.items() if k != newcomer},
        )
        found = {n for n, _ in res.nearest}
        # count a hit if we found a node as close as the true neighbour (ties in one DC)
        worst_true = world.base_rtt(newcomer, truth[-1])
        hits = sum(world.base_rtt(newcomer, n) <= worst_true * 1.2 + 0.5 for n in found)
        recalls.append(hits / 3)
        probes.append(res.probes)
    assert statistics.mean(recalls) > 0.9
    assert statistics.mean(probes) < 0.25 * len(existing)


def test_swap_or_not_is_a_permutation_and_epochs_cover_everyone() -> None:
    n = 50
    perm = [swap_or_not_index(i, n, b"seed") for i in range(n)]
    assert sorted(perm) == list(range(n))
    assert perm != list(range(n))
    nodes = [f"v{i}" for i in range(n)]
    ep = EpochPermutation(nodes, b"s")
    first_epoch = ep.draw(25) + ep.draw(25)
    assert sorted(first_epoch) == sorted(nodes)
    assert ep.draw(5) != first_epoch[:5]  # new epoch, new order


def test_invalid_k() -> None:
    with pytest.raises(ValueError):
        uniform(["a"], 2, random.Random(0))
