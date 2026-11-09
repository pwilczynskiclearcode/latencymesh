"""titanoboa harness that mirrors a simulated network on an in-process EVM."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import boa

from ..genesis import Genesis
from ..model import Location, Measurement, Node, NodeId
from . import CONTRACTS_DIR

ETHER = 10**18
STATUS_TRUSTED, STATUS_PENDING, STATUS_ACTIVE, STATUS_REJECTED = 1, 2, 4, 8


def load_contract(name_or_path: str | Path, *args: Any, **kwargs: Any) -> Any:
    """Deploy a bundled contract by name (``"NodeRegistry"``) or any ``.vy`` path."""
    p = Path(name_or_path)
    if p.suffix != ".vy":
        p = CONTRACTS_DIR / f"{name_or_path}.vy"
    return boa.load(str(p), *args, **kwargs)


def encode_coord(deg: float) -> int:
    return round(deg * 1_000_000)


def encode_rtt(ms: float) -> int:
    """RTT in whole microseconds (uint32 covers > 70 minutes)."""
    return max(0, min(2**32 - 1, round(ms * 1000)))


def decode_rtt(us: int) -> float:
    return us / 1000.0


@dataclass
class MeshChain:
    """A NodeRegistry deployment plus a stable NodeId → account mapping.

    Every node gets its own funded account, so contract calls are made *as* that node
    (``boa.env.prank``), exactly like independent operators would.
    """

    min_stake: int = ETHER
    seed: int = 0
    funding: int = 1000 * ETHER
    accounts: dict[NodeId, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.owner = boa.env.eoa
        self.registry = load_contract("NodeRegistry", self.min_stake)

    # -- accounts --------------------------------------------------------------
    def address(self, node_id: NodeId) -> str:
        a = self.accounts.get(node_id)
        if a is None:
            a = boa.env.generate_address(alias=node_id)
            boa.env.set_balance(a, self.funding)
            self.accounts[node_id] = a
        return a

    def node_of(self, address: str) -> NodeId:
        for nid, a in self.accounts.items():
            if a == address:
                return nid
        raise KeyError(address)

    @contextmanager
    def as_node(self, node_id: NodeId) -> Iterator[str]:
        a = self.address(node_id)
        with boa.env.prank(a):
            yield a

    # -- registry --------------------------------------------------------------
    def register_genesis(self, genesis: Genesis, seal: bool = True) -> None:
        for n in genesis.nodes:
            loc = _require_location(n)
            self.registry.register_genesis(
                self.address(n.id), encode_coord(loc.lat), encode_coord(loc.lon), loc.asn or 0
            )
        if seal:
            self.registry.seal_genesis()

    def join(self, node: Node, stake: int | None = None) -> None:
        loc = _require_location(node)
        with self.as_node(node.id):
            self.registry.join(
                encode_coord(loc.lat),
                encode_coord(loc.lon),
                loc.asn or 0,
                value=self.min_stake if stake is None else stake,
            )

    def status(self, node_id: NodeId) -> int:
        return int(self.registry.nodes(self.address(node_id)).status)

    def stake(self, node_id: NodeId) -> int:
        return int(self.registry.nodes(self.address(node_id)).stake)

    def set_status(self, node_id: NodeId, status: int) -> None:
        self.registry.set_status(self.address(node_id), status)

    # -- commit-reveal -----------------------------------------------------------
    def open_round(self, commit_blocks: int = 10, reveal_blocks: int = 10) -> int:
        return int(self.registry.open_round(commit_blocks, reveal_blocks))

    def salt(self, round_id: int, observer: NodeId, target: NodeId) -> bytes:
        """Deterministic per-measurement salt (a real node would use fresh randomness)."""
        return hashlib.sha256(f"{self.seed}|{round_id}|{observer}|{target}".encode()).digest()

    def commitment(self, round_id: int, m: Measurement) -> bytes:
        return bytes(
            self.registry.measurement_hash(
                round_id,
                self.address(m.observer),
                self.address(m.target),
                encode_rtt(m.rtt_ms),
                self.salt(round_id, m.observer, m.target),
            )
        )

    def commit(self, round_id: int, m: Measurement) -> None:
        c = self.commitment(round_id, m)
        with self.as_node(m.observer):
            self.registry.commit(round_id, self.address(m.target), c)

    def reveal(self, round_id: int, m: Measurement) -> None:
        with self.as_node(m.observer):
            self.registry.reveal(
                round_id,
                self.address(m.target),
                encode_rtt(m.rtt_ms),
                self.salt(round_id, m.observer, m.target),
            )

    def revealed(self, round_id: int, observer: NodeId, target: NodeId) -> float | None:
        o, t = self.address(observer), self.address(target)
        if not self.registry.is_revealed(round_id, o, t):
            return None
        return decode_rtt(int(self.registry.revealed_rtt(round_id, o, t)))

    def to_reveal_phase(self, round_id: int) -> None:
        self.mine_until(int(self.registry.rounds(round_id).commit_end) + 1)

    def to_round_end(self, round_id: int) -> None:
        self.mine_until(int(self.registry.rounds(round_id).reveal_end) + 1)

    @staticmethod
    def mine(blocks: int = 1) -> None:
        boa.env.time_travel(blocks=blocks)

    @staticmethod
    def block_number() -> int:
        return int(boa.env.evm.patch.block_number)

    def mine_until(self, block: int) -> None:
        n = block - self.block_number()
        if n > 0:
            self.mine(n)


def _require_location(node: Node) -> Location:
    if node.location is None:
        raise ValueError(f"node {node.id!r} has no (claimed) location")
    return node.location
