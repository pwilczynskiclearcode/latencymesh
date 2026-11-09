"""Reference join protocol, run end-to-end on the simulated network and the EVM.

1. The newcomer finds its nearest **genesis** node off-chain (it probes a few trusted
   nodes, :func:`latencymesh.select.iterative_nearest`) and names it as *anchor*.
2. It stakes and joins; the registry fixes a *future* beacon round.
3. Once the beacon publishes that round, anyone triggers the on-chain draw of
   witnesses from distinct regions - the newcomer has no say in who measures it.
4. Newcomer ↔ every witness and newcomer ↔ anchor are measured in both directions,
   committed, then revealed.
5. Optionally an off-chain location check (CBG) lets the owner veto a node whose
   claimed coordinates contradict the witnesses' RTTs.
6. Anyone finalises: the contract compares both sides of every pair, admits the
   newcomer on a quorum, and penalises outvoted or silent non-genesis participants.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ..geoloc import ClaimCheck, Landmark, verify_claim
from ..model import Measurement, NodeId
from ..select import iterative_nearest, make_distance
from ..sim import Simulation
from .chain import MeshChain


@dataclass(slots=True)
class JoinOutcome:
    newcomer: NodeId
    round_id: int
    anchor: NodeId
    witnesses: list[NodeId]
    accepted: bool = False
    vetoed: bool = False
    region: int | None = None
    agreeing: list[NodeId] = field(default_factory=list)
    disagreeing: list[NodeId] = field(default_factory=list)
    silent: list[NodeId] = field(default_factory=list)
    penalties: dict[NodeId, int] = field(default_factory=dict)
    claim: ClaimCheck | None = None


def find_anchor(sim: Simulation, newcomer: NodeId, trusted: Sequence[NodeId], seed: int) -> NodeId:
    """The newcomer's own search for its nearest genesis node (off-chain, cheap)."""
    rng = random.Random(seed)
    res = iterative_nearest(
        lambda c: sim.world.ping(newcomer, c, rng),
        list(trusted),
        make_distance(graph=sim.graph),
        rng,
        x=8,
        t=1,
    )
    return res.nearest[0][0]


def run_join_round(
    sim: Simulation,
    chain: MeshChain,
    newcomer: NodeId,
    anchor: NodeId | None = None,
    landmarks: Mapping[NodeId, Landmark] | None = None,
    withhold_reveal: Sequence[NodeId] = (),
    stake: int | None = None,
) -> JoinOutcome:
    """Run one admission round; ``withhold_reveal`` lists participants that never reveal."""
    trusted = sim.world.trusted_ids()
    anchor = anchor or find_anchor(sim, newcomer, trusted, chain.seed)
    sim.join(newcomer)
    rid = chain.join(sim.world.nodes[newcomer], anchor, stake)
    chain.publish_randomness()
    witnesses = chain.assign_witnesses(rid)
    out = JoinOutcome(newcomer, rid, anchor, witnesses)

    measured: list[Measurement] = []
    for p in [*witnesses, anchor]:
        for m in (sim.measure(p, newcomer), sim.measure(newcomer, p)):
            if m is not None:
                chain.commit(rid, m)
                measured.append(m)

    chain.to_reveal_phase(rid)
    for m in measured:
        if m.observer not in withhold_reveal:
            chain.reveal(rid, m)

    for p in [*witnesses, anchor]:
        by_p, by_n = chain.revealed(rid, p, newcomer), chain.revealed(rid, newcomer, p)
        if by_p is None:
            out.silent.append(p)
        elif by_n is not None and _agree(chain, by_p, by_n):
            out.agreeing.append(p)
        else:
            out.disagreeing.append(p)

    if landmarks is not None:
        claimed = sim.world.nodes[newcomer].location
        rtts = {
            p: v
            for p in [*witnesses, anchor]
            if (v := chain.revealed(rid, p, newcomer)) is not None
        }
        if claimed is not None and rtts:
            out.claim = verify_claim(claimed, rtts, landmarks)
            if not out.claim.ok:
                chain.registry.veto(chain.address(newcomer))
                out.vetoed = True
                return out

    chain.to_round_end(rid)
    out.accepted, out.penalties = chain.finalize(rid)
    if out.accepted:
        out.region = chain.region(newcomer)
    return out


def _agree(chain: MeshChain, a_ms: float, b_ms: float) -> bool:
    """Same rule as NodeRegistry._agree, for reporting only (the contract decides)."""
    p = chain.registry.params()
    lo, diff = min(a_ms, b_ms), abs(a_ms - b_ms)
    return bool(diff * 1000 <= max(p.tolerance_abs_us, lo * 1000 * p.tolerance_bps / 10_000))
