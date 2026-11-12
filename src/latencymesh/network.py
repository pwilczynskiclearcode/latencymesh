"""Load a network description and deploy it into the WitnessMesh contract."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import boa
import yaml

PACKAGE_DIR = Path(__file__).parent
CONTRACT = PACKAGE_DIR / "contracts" / "WitnessMesh.vy"
NETWORKS_DIR = PACKAGE_DIR / "networks"
K = 3  # nearest neighbours per node, must match the contract


@dataclass(frozen=True)
class Node:
    id: str
    provider: str = ""
    region: str = ""
    city: str = ""
    country: str = ""
    lat: float = 0.0
    lon: float = 0.0


@dataclass(frozen=True)
class Network:
    """Nodes and, for each node, its nearest nodes with RTT in ms (closest first)."""

    name: str
    nodes: dict[str, Node]
    nearest: dict[str, dict[str, float]]
    rtt_source: str = "estimated"

    def nearest_k(self, node_id: str) -> list[tuple[str, float]]:
        ranked = sorted(self.nearest[node_id].items(), key=lambda kv: (kv[1], kv[0]))
        return ranked[:K]


def load_network(name_or_path: str | Path) -> Network:
    """Load ``nodes.yaml`` and ``rtt.yaml`` of a bundled network (by name) or a folder."""
    folder = Path(name_or_path)
    if not folder.is_dir():
        folder = NETWORKS_DIR / str(name_or_path)
    spec = yaml.safe_load((folder / "nodes.yaml").read_text())
    rtt = yaml.safe_load((folder / "rtt.yaml").read_text())
    nodes = {n["id"]: Node(**n) for n in spec["nodes"]}
    if len(nodes) != len(spec["nodes"]):
        raise ValueError("duplicate node id")
    return Network(spec["name"], nodes, rtt["nearest"], rtt.get("source", "unknown"))


def ms_to_us(rtt_ms: float) -> int:
    return round(rtt_ms * 1000)


def seed(text: str | int) -> bytes:
    """A 32-byte seed from a readable label, e.g. ``seed("cycle 7")``."""
    return hashlib.sha256(str(text).encode()).digest()


@dataclass
class Mesh:
    """A deployed WitnessMesh plus the mapping between node ids and addresses."""

    contract: Any
    network: Network
    address: dict[str, str] = field(default_factory=dict)
    _name: dict[str, str] = field(default_factory=dict)

    def name(self, address: Any) -> str:
        return self._name[str(address)]

    def names(self, addresses: Any) -> list[str]:
        return [self.name(a) for a in addresses]

    def lines_of(self, node_id: str) -> tuple[list[str], list[str], list[str]]:
        l1, l2, l3 = self.contract.lines_of(self.address[node_id])
        return self.names(l1), self.names(l2), self.names(l3)

    def witnesses_for(self, node_id: str, seed_: bytes) -> list[tuple[str, int]]:
        """The 4 witnesses of a node as ``(node id, line)``; line 0 means whole network."""
        chosen, origin = self.contract.witnesses_for(self.address[node_id], seed_)
        return list(zip(self.names(chosen), [int(o) for o in origin], strict=True))


def deploy(network: Network, seal: bool = True) -> Mesh:
    """Deploy WitnessMesh and load the network as its genesis."""
    mesh = Mesh(boa.load(str(CONTRACT)), network)
    for node_id in network.nodes:
        addr = str(boa.env.generate_address(alias=node_id))
        mesh.address[node_id] = addr
        mesh._name[addr] = node_id
        mesh.contract.add_node(addr)
    for node_id in network.nodes:
        ranked = network.nearest_k(node_id)
        mesh.contract.set_nearest(
            mesh.address[node_id],
            [mesh.address[n] for n, _ in ranked],
            [ms_to_us(rtt) for _, rtt in ranked],
        )
    if seal:
        mesh.contract.seal()
    return mesh
