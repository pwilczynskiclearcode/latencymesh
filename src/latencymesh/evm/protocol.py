"""Reference join protocol, run end-to-end on the simulated network and the EVM.

1. The newcomer stakes and registers its *claimed* location (``PENDING``).
2. Witnesses are drawn with public randomness after the join (see :mod:`latencymesh.select`).
3. Both sides of every (witness, newcomer) pair measure RTT and **commit** the hash.
4. After the commit phase both sides **reveal**; nobody could copy or adapt to the other.
5. Pairs are checked for agreement (relative *and* absolute tolerance), the witnesses'
   RTTs are checked against the claimed location (:func:`latencymesh.geoloc.verify_claim`),
   and the owner records ``ACTIVE`` or ``REJECTED``.
6. Anyone can slash observers that committed but did not reveal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from ..geoloc import ClaimCheck, Landmark, verify_claim
from ..model import Measurement, NodeId
from ..sim import Simulation
from .chain import STATUS_ACTIVE, STATUS_REJECTED, MeshChain


@dataclass(frozen=True, slots=True)
class Tolerance:
    """Two RTTs agree if they differ by at most ``max(abs_ms, rel · min(a, b))``."""

    rel: float = 0.2
    abs_ms: float = 1.5
    #: share of witness pairs that must agree
    quorum: float = 0.8

    def agree(self, a: float, b: float) -> bool:
        return abs(a - b) <= max(self.abs_ms, self.rel * min(a, b))


@dataclass(slots=True)
class JoinOutcome:
    newcomer: NodeId
    round_id: int
    accepted: bool
    agreeing: list[NodeId] = field(default_factory=list)
    disagreeing: list[NodeId] = field(default_factory=list)
    missing: list[NodeId] = field(default_factory=list)
    claim: ClaimCheck | None = None
    slashed: list[NodeId] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


Behaviour = Callable[[Measurement], Measurement | None]


def run_join_round(
    sim: Simulation,
    chain: MeshChain,
    newcomer: NodeId,
    witnesses: Sequence[NodeId],
    landmarks: Mapping[NodeId, Landmark] | None = None,
    tolerance: Tolerance | None = None,
    withhold_reveal: Sequence[NodeId] = (),
) -> JoinOutcome:
    """Run one admission round; ``withhold_reveal`` lists observers that never reveal."""
    tol = tolerance or Tolerance()
    rid = chain.open_round()
    pairs: list[tuple[Measurement, Measurement]] = []
    for w in witnesses:
        by_w, by_n = sim.measure(w, newcomer), sim.measure(newcomer, w)
        if by_w is None or by_n is None:
            continue
        chain.commit(rid, by_w)
        chain.commit(rid, by_n)
        pairs.append((by_w, by_n))

    chain.to_reveal_phase(rid)
    for by_w, by_n in pairs:
        for m in (by_w, by_n):
            if m.observer not in withhold_reveal:
                chain.reveal(rid, m)

    out = JoinOutcome(newcomer, rid, accepted=False)
    witness_rtts: dict[NodeId, float] = {}
    for w in witnesses:
        rw, rn = chain.revealed(rid, w, newcomer), chain.revealed(rid, newcomer, w)
        if rw is None or rn is None:
            out.missing.append(w)
            continue
        witness_rtts[w] = rw
        (out.agreeing if tol.agree(rw, rn) else out.disagreeing).append(w)

    decided = len(out.agreeing) + len(out.disagreeing)
    if newcomer in withhold_reveal or decided == 0:
        out.reasons.append("newcomer did not reveal")
    elif len(out.agreeing) / decided < tol.quorum:
        out.reasons.append(f"only {len(out.agreeing)}/{decided} witnesses agree")
    if landmarks is not None:
        claimed = sim.graph.node(newcomer).location
        if claimed is None:
            out.reasons.append("no claimed location")
        else:
            out.claim = verify_claim(claimed, witness_rtts, landmarks)
            if not out.claim.ok:
                out.reasons.append("claimed location inconsistent with witness RTTs")
    out.accepted = not out.reasons
    unrevealed = {
        m.observer
        for pair in pairs
        for m in pair
        if chain.revealed(rid, m.observer, m.target) is None
    }
    if unrevealed:  # slash before the status change can refund the newcomer's stake
        chain.to_round_end(rid)
        for by_w, by_n in pairs:
            for m in (by_w, by_n):
                if m.observer in unrevealed:
                    chain.registry.slash_non_revealer(
                        rid, chain.address(m.observer), chain.address(m.target)
                    )
        out.slashed = sorted(unrevealed)
    chain.set_status(newcomer, STATUS_ACTIVE if out.accepted else STATUS_REJECTED)
    return out
