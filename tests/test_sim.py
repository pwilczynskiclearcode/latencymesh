import dataclasses

import pytest

from latencymesh.model import Measurement
from latencymesh.sim import EventLoop, RandomPairs, Simulation
from latencymesh.synth import WorldConfig, generate_world


def small_sim(**kw: object) -> Simulation:
    world = generate_world(WorldConfig(n_nodes=60, n_trusted=20, seed=11))
    return Simulation.from_genesis(world, seed=1, **kw)  # type: ignore[arg-type]


def test_event_loop_orders_by_time_then_insertion() -> None:
    loop = EventLoop()
    seen: list[str] = []
    loop.schedule(2, lambda: seen.append("b"))
    loop.schedule(1, lambda: seen.append("a1"))
    loop.schedule(1, lambda: seen.append("a2"))
    loop.run(1.5)
    assert seen == ["a1", "a2"] and loop.now == 1.5
    loop.run(10)
    assert seen[-1] == "b"


def test_genesis_graph_is_complete_for_trusted_nodes() -> None:
    sim = small_sim()
    assert sim.graph.edge_count == 20 * 19 // 2
    assert len(sim.graph) == 60


def test_continuous_probing_grows_and_refreshes_graph() -> None:
    sim = small_sim(probe_budget=4, tick_interval=60)
    before = sim.graph.edge_count
    sim.run(600)
    assert sim.stats.ticks == 11  # t = 0, 60, ..., 600
    assert sim.graph.edge_count > before + 200
    assert all(sim.graph.degree(n) > 0 for n in sim.graph.node_ids)
    # estimates stay close to ground truth
    errs = [
        abs(rtt - sim.world.base_rtt(a, b)) / sim.world.base_rtt(a, b)
        for a, b, rtt in sim.graph.edges()
    ]
    assert sorted(errs)[len(errs) // 2] < 0.05


def test_random_pairs_policy_respects_budget() -> None:
    sim = small_sim(policy=RandomPairs(), probe_budget=3)
    probes = sim.policy.plan(sim, 3, sim.rng)
    assert len(probes) == 3 * 60
    assert all(a != b for a, b in probes)


def test_lying_and_lazy_reporters() -> None:
    sim = small_sim()
    liar, lazy = "n00030", "n00031"
    sim.reporters[liar] = lambda m: dataclasses.replace(m, rtt_ms=m.rtt_ms * 3)
    sim.reporters[lazy] = lambda m: None
    seen: list[Measurement] = []
    sim.on_measurement(seen.append)

    m_liar, m_honest = sim.measure_both(liar, "n00000")
    assert m_liar is not None and m_honest is not None
    assert m_liar.rtt_ms == pytest.approx(3 * m_honest.rtt_ms, rel=0.2)
    edge = sim.graph.edge(liar, "n00000")
    assert edge is not None and edge.disagreement() > 0.5

    assert sim.measure(lazy, "n00000") is None
    assert sim.stats.dropped == 1 and len(seen) == 2
