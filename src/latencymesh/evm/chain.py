"""titanoboa harness that mirrors a simulated network on an in-process EVM."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import boa

from ..cluster import regions_from_rtt
from ..genesis import Genesis
from ..model import Location, Measurement, Node, NodeId
from . import CONTRACTS_DIR

ETHER = 10**18
STATUS_TRUSTED, STATUS_PENDING, STATUS_ACTIVE, STATUS_REJECTED = 1, 2, 4, 8
WEIGHT_REGION_UNIFORM, WEIGHT_SQRT, WEIGHT_NODE_UNIFORM = 0, 1, 2
ZERO_ADDRESS = "0x" + "00" * 20


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
    (``boa.env.prank``), exactly like independent operators would. A ``MockBeacon``
    provides randomness; :meth:`publish_randomness` plays the role of the beacon network.
    """

    min_stake: int = ETHER
    seed: int = 0
    funding: int = 1000 * ETHER
    accounts: dict[NodeId, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.owner = boa.env.eoa
        self.beacon = load_contract("MockBeacon")
        self.registry = load_contract("NodeRegistry", self.beacon.address, self.min_stake)
        self._by_address: dict[str, NodeId] = {}

    # -- accounts --------------------------------------------------------------
    def address(self, node_id: NodeId) -> str:
        a = self.accounts.get(node_id)
        if a is None:
            a = str(boa.env.generate_address(alias=node_id))
            boa.env.set_balance(a, self.funding)
            self.accounts[node_id] = a
            self._by_address[a.lower()] = node_id
        return a

    def node_of(self, address: Any) -> NodeId:
        return self._by_address[str(address).lower()]

    @contextmanager
    def as_node(self, node_id: NodeId) -> Iterator[str]:
        a = self.address(node_id)
        with boa.env.prank(a):
            yield a

    # -- configuration -----------------------------------------------------------
    def set_params(self, **changes: int) -> None:
        self.registry.set_params(tuple(self.registry.params()._replace(**changes)))

    # -- registry --------------------------------------------------------------
    def register_genesis(
        self,
        genesis: Genesis,
        regions: Mapping[NodeId, int] | None = None,
        region_rtt_ms: float = 25.0,
        seal: bool = True,
    ) -> dict[NodeId, int]:
        """Register trusted nodes; regions default to an RTT-only clustering of the genesis."""
        ids = [n.id for n in genesis.nodes]
        if regions is None:
            graph = genesis.to_graph()
            regions = regions_from_rtt(ids, graph.rtt, region_rtt_ms)
        for n in sorted(genesis.nodes, key=lambda n: (regions[n.id], ids.index(n.id))):
            loc = n.location or Location(0.0, 0.0)
            self.registry.register_genesis(
                self.address(n.id),
                regions[n.id],
                encode_coord(loc.lat),
                encode_coord(loc.lon),
                loc.asn or 0,
            )
        if seal:
            self.registry.seal_genesis()
        return dict(regions)

    def join(self, node: Node, anchor: NodeId, stake: int | None = None) -> int:
        loc = node.location or Location(0.0, 0.0)
        with self.as_node(node.id):
            rid = self.registry.join(
                self.address(anchor),
                encode_coord(loc.lat),
                encode_coord(loc.lon),
                loc.asn or 0,
                value=self.min_stake if stake is None else stake,
            )
        return int(rid)

    def info(self, node_id: NodeId) -> Any:
        return self.registry.nodes(self.address(node_id))

    def status(self, node_id: NodeId) -> int:
        return int(self.info(node_id).status)

    def stake(self, node_id: NodeId) -> int:
        return int(self.info(node_id).stake)

    def region(self, node_id: NodeId) -> int:
        return int(self.info(node_id).region)

    def accordance(self, node_id: NodeId) -> float:
        return int(self.registry.accordance_bps(self.address(node_id))) / 10_000

    # -- randomness and witnesses -------------------------------------------------
    def publish_randomness(self) -> int:
        """Publish the next beacon round (deterministic from ``seed`` for reproducibility)."""
        r = int(self.beacon.latest_round()) + 1
        value = hashlib.sha256(f"beacon|{self.seed}|{r}".encode()).digest()
        self.beacon.publish(r, value)
        return r

    def assign_witnesses(self, round_id: int) -> list[NodeId]:
        ws = self.registry.assign_witnesses(round_id)
        return [self.node_of(w) for w in ws]

    def preview_witnesses(
        self, seed: bytes, k: int, exclude: Sequence[NodeId] = ()
    ) -> list[NodeId]:
        ws = self.registry.preview_witnesses(seed, k, [self.address(e) for e in exclude])
        return [self.node_of(w) for w in ws]

    def round_info(self, round_id: int) -> Any:
        return self.registry.round_info(round_id)

    # -- commit-reveal -----------------------------------------------------------
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

    def finalize(self, round_id: int) -> tuple[bool, dict[NodeId, int]]:
        """Finalise a round; returns (admitted, penalties taken per node)."""
        admitted = bool(self.registry.finalize(round_id))
        penalties: dict[NodeId, int] = {}
        for ev in self.registry.get_logs():
            if type(ev).__name__ == "Penalised":
                penalties[self.node_of(ev.node)] = int(ev.amount)
        return admitted, penalties

    # -- blocks --------------------------------------------------------------------
    def to_reveal_phase(self, round_id: int) -> None:
        self.mine_until(int(self.round_info(round_id).commit_end) + 1)

    def to_round_end(self, round_id: int) -> None:
        self.mine_until(int(self.round_info(round_id).reveal_end) + 1)

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
