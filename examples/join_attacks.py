"""End-to-end example: honest and malicious nodes try to join a latency-verified network.

Run with:  python examples/join_attacks.py
"""

from __future__ import annotations

import dataclasses

from latencymesh.evm.chain import MeshChain
from latencymesh.evm.protocol import run_join_round
from latencymesh.geoloc import calibrate
from latencymesh.model import Location
from latencymesh.select import Beacon, dispersed, make_distance
from latencymesh.sim import Simulation
from latencymesh.synth import WorldConfig, generate_world

WARSAW = Location(52.23, 21.01, "PL", "Warsaw", asn=65001)
TOKYO = Location(35.68, 139.69, "JP", "Tokyo", asn=65002)


def main() -> None:
    world = generate_world(WorldConfig(n_nodes=60, n_trusted=40, seed=8))
    genesis = world.genesis()
    sim = Simulation(world=world, graph=genesis.to_graph(), seed=2)
    chain = MeshChain()
    chain.register_genesis(genesis)
    landmarks = calibrate(genesis)
    beacon = Beacon(seed=2026)

    scenarios = {
        "honest": (WARSAW, WARSAW, None, 0.0),
        "rtt-liar": (WARSAW, WARSAW, 0.5, 0.0),
        "fake-tokyo": (WARSAW, TOKYO, None, 0.0),
        "delay-tokyo": (WARSAW, TOKYO, None, 45.0),
    }
    for name, (true_loc, claimed, scale, delay) in scenarios.items():
        node = world.add_node(name, true_loc, 0.3, claimed=claimed)
        sim.join(name)
        chain.join(node)
        if scale is not None:
            sim.reporters[name] = lambda m, s=scale: dataclasses.replace(m, rtt_ms=m.rtt_ms * s)
        world.added_delay_ms[name] = delay
        witnesses = dispersed(
            world.trusted_ids(), 12, make_distance(graph=sim.graph), beacon.rng(name)
        )
        out = run_join_round(sim, chain, name, witnesses, landmarks=landmarks)
        verdict = "ADMITTED" if out.accepted else "REJECTED"
        print(f"{name:12s} {verdict:9s} {'; '.join(out.reasons) or 'all checks passed'}")


if __name__ == "__main__":
    main()
