"""Sparse, undirected RTT graph built from directed measurements.

Storing all ``n·(n-1)/2`` pairs does not scale to 10k nodes, so the graph only keeps the
pairs that were actually measured. Each edge keeps a short window of recent observations
per observer; the edge estimate is the minimum over that window (robust to queueing
spikes, yet it follows routing changes once old samples fall out of the window).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from .model import Measurement, Node, NodeId

Pair = tuple[NodeId, NodeId]


def pair(a: NodeId, b: NodeId) -> Pair:
    """Canonical (sorted) key of an undirected edge."""
    return (a, b) if a <= b else (b, a)


@dataclass(slots=True)
class EdgeStats:
    """Observations of one undirected edge, kept per observer."""

    window: int
    by_observer: dict[NodeId, deque[float]] = field(default_factory=dict)
    last_time: float = -math.inf
    count: int = 0

    def add(self, m: Measurement) -> None:
        q = self.by_observer.get(m.observer)
        if q is None:
            q = self.by_observer[m.observer] = deque(maxlen=self.window)
        q.append(m.rtt_ms)
        self.last_time = max(self.last_time, m.time)
        self.count += 1

    def observer_rtt(self, observer: NodeId) -> float | None:
        q = self.by_observer.get(observer)
        return min(q) if q else None

    @property
    def rtt(self) -> float:
        """Edge estimate: minimum of the per-observer windowed minima."""
        return min(min(q) for q in self.by_observer.values())

    def disagreement(self) -> float:
        """Relative spread between observers, 0 when only one side measured."""
        vals = [min(q) for q in self.by_observer.values()]
        if len(vals) < 2:
            return 0.0
        lo, hi = min(vals), max(vals)
        return (hi - lo) / hi if hi > 0 else 0.0


class RTTGraph:
    """Nodes plus the measured subset of their pairwise RTTs."""

    def __init__(self, window: int = 8) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        self.window = window
        self._nodes: dict[NodeId, Node] = {}
        self._edges: dict[Pair, EdgeStats] = {}
        self._adj: dict[NodeId, set[NodeId]] = {}

    # -- nodes -----------------------------------------------------------------
    def add_node(self, node: Node) -> None:
        if node.id in self._nodes:
            raise ValueError(f"duplicate node id {node.id!r}")
        self._nodes[node.id] = node
        self._adj[node.id] = set()

    def remove_node(self, node_id: NodeId) -> None:
        for other in self._adj.pop(node_id):
            self._adj[other].discard(node_id)
            del self._edges[pair(node_id, other)]
        del self._nodes[node_id]

    def node(self, node_id: NodeId) -> Node:
        return self._nodes[node_id]

    def __contains__(self, node_id: object) -> bool:
        return node_id in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    @property
    def node_ids(self) -> list[NodeId]:
        return list(self._nodes)

    def nodes(self) -> Iterator[Node]:
        return iter(self._nodes.values())

    def trusted_ids(self) -> list[NodeId]:
        return [n.id for n in self._nodes.values() if n.trusted]

    # -- edges -----------------------------------------------------------------
    def record(self, m: Measurement) -> None:
        if m.observer not in self._nodes or m.target not in self._nodes:
            raise KeyError(f"unknown node in measurement {m.observer}->{m.target}")
        key = pair(m.observer, m.target)
        stats = self._edges.get(key)
        if stats is None:
            stats = self._edges[key] = EdgeStats(self.window)
            self._adj[m.observer].add(m.target)
            self._adj[m.target].add(m.observer)
        stats.add(m)

    def record_many(self, ms: Iterable[Measurement]) -> None:
        for m in ms:
            self.record(m)

    def rtt(self, a: NodeId, b: NodeId) -> float | None:
        if a == b:
            return 0.0
        stats = self._edges.get(pair(a, b))
        return stats.rtt if stats else None

    def edge(self, a: NodeId, b: NodeId) -> EdgeStats | None:
        return self._edges.get(pair(a, b))

    def neighbors(self, node_id: NodeId) -> dict[NodeId, float]:
        """Measured neighbours of a node with their RTT estimates."""
        return {o: self._edges[pair(node_id, o)].rtt for o in self._adj[node_id]}

    def nearest_measured(self, node_id: NodeId, k: int) -> list[tuple[NodeId, float]]:
        return sorted(self.neighbors(node_id).items(), key=lambda kv: (kv[1], kv[0]))[:k]

    def edges(self) -> Iterator[tuple[NodeId, NodeId, float]]:
        for (a, b), s in self._edges.items():
            yield a, b, s.rtt

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def degree(self, node_id: NodeId) -> int:
        return len(self._adj[node_id])

    def stale_edges(self, older_than: float) -> list[Pair]:
        return [k for k, s in self._edges.items() if s.last_time < older_than]

    def to_matrix(self, ids: Sequence[NodeId]) -> npt.NDArray[np.float64]:
        """Dense RTT matrix for ``ids``; unmeasured pairs are ``nan``."""
        n = len(ids)
        out = np.full((n, n), np.nan)
        np.fill_diagonal(out, 0.0)
        for i in range(n):
            for j in range(i + 1, n):
                v = self.rtt(ids[i], ids[j])
                if v is not None:
                    out[i, j] = out[j, i] = v
        return out
