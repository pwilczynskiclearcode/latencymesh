"""End-to-end example: honest and malicious nodes try to join a latency-verified network.

The contract draws geographically diverse witnesses from beacon randomness; the newcomer
and the witnesses measure each other, commit, reveal, and the contract decides.

Run with:  python examples/join_attacks.py
"""

from __future__ import annotations

import dataclasses

from latencymesh.evm.chain import MeshChain
from latencymesh.evm.protocol import run_join_round
from latencymesh.geoloc import calibrate
from latencymesh.model import Location
from latencymesh.sim import Simulation
from latencymesh.synth import WorldConfig, generate_world

WARSAW = Location(52.23, 21.01, "PL", "Warsaw", asn=65001)
TOKYO = Location(35.68, 139.69, "JP", "Tokyo", asn=65002)


def main() -> None:
    world = generate_world(WorldConfig(n_nodes=72, n_trusted=72, seed=8))
    genesis = world.genesis()
    sim = Simulation(world=world, graph=genesis.to_graph(), seed=2)
    chain = MeshChain()
    regions = chain.register_genesis(genesis)
    landmarks = calibrate(genesis)
    print(f"genesis: {len(genesis.nodes)} trusted nodes in {len(set(regions.values()))} regions\n")

    scenarios = {
        "honest": (WARSAW, WARSAW, None, 0.0),
        "rtt-liar": (WARSAW, WARSAW, 0.5, 0.0),
        "fake-tokyo": (WARSAW, TOKYO, None, 0.0),
        "delay-tokyo": (WARSAW, TOKYO, None, 45.0),
    }
    for name, (true_loc, claimed, scale, delay) in scenarios.items():
        world.add_node(name, true_loc, 0.3, claimed=claimed)
        if scale is not None:
            sim.reporters[name] = lambda m, s=scale: dataclasses.replace(m, rtt_ms=m.rtt_ms * s)
        world.added_delay_ms[name] = delay
        out = run_join_round(sim, chain, name, landmarks=landmarks)
        cities = ", ".join(sorted({world.true_location[w].city or "?" for w in out.witnesses}))
        if out.accepted:
            verdict = f"ADMITTED  region {out.region}"
        elif out.vetoed:
            verdict = "VETOED    claimed location contradicts witness RTTs"
        else:
            lost = ", ".join(f"{n} {v / 1e18:.2f} ETH" for n, v in out.penalties.items() if v)
            verdict = f"REJECTED  penalised: {lost}"
        print(f"{name:12s} {verdict}")
        print(f"{'':12s} witnesses: {cities}\n")


if __name__ == "__main__":
    main()
