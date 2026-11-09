"""Hierarchical latency clustering: datacenter → city → country → continent.

Nodes are clustered bottom-up with average-linkage agglomeration, cut at a latency
threshold per level. Higher levels cluster the *clusters* of the level below
(average distance between members), so each level is consistent with the previous
one and the work shrinks as we go up.

Access-link delay (home connections add several ms on both ends) blurs everything, so
when coordinates with heights are supplied the *core* distance
``max(0, rtt − h_a − h_b)`` is used instead of the raw RTT.

The default thresholds are deliberately rough. RTT alone separates continents and
cities well, but countries only coarsely: two capitals 7 ms apart can be in different
countries while two cities of one large country can be 60 ms apart. Treat the country
level as "regional cluster" unless calibrated with trusted nodes.

Complexity: O(n²) memory and roughly O(n³) time at the finest level - fine for a few
thousand nodes; see the roadmap for scaling plans.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from .coords.base import Coordinate
from .model import NodeId
from .select import Distance

Matrix = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class Level:
    name: str
    max_rtt_ms: float


DEFAULT_LEVELS: tuple[Level, ...] = (
    Level("datacenter", 0.8),
    Level("city", 4.0),
    Level("country", 15.0),
    Level("continent", 70.0),
)


def average_linkage(dist: Matrix, threshold: float) -> list[list[int]]:
    """Agglomerate items while the closest pair of clusters is within ``threshold``."""
    n = dist.shape[0]
    d = dist.astype(float).copy()
    np.fill_diagonal(d, np.inf)
    sizes = np.ones(n)
    alive = np.ones(n, dtype=bool)
    members: list[list[int]] = [[i] for i in range(n)]
    while True:
        idx = int(np.argmin(d))
        i, j = divmod(idx, n)
        if not np.isfinite(d[i, j]) or d[i, j] > threshold:
            break
        # Lance–Williams update for average linkage: merge j into i
        new = (sizes[i] * d[i] + sizes[j] * d[j]) / (sizes[i] + sizes[j])
        d[i, :] = new
        d[:, i] = new
        d[i, i] = np.inf
        d[j, :] = np.inf
        d[:, j] = np.inf
        sizes[i] += sizes[j]
        alive[j] = False
        members[i].extend(members[j])
        members[j] = []
    return [sorted(members[i]) for i in range(n) if alive[i]]


@dataclass(slots=True)
class Clustering:
    levels: tuple[Level, ...]
    #: per level: node -> cluster index
    labels: dict[str, dict[NodeId, int]] = field(default_factory=dict)

    def cluster_of(self, level: str, node: NodeId) -> int:
        return self.labels[level][node]

    def same(self, level: str, a: NodeId, b: NodeId) -> bool:
        return self.labels[level][a] == self.labels[level][b]

    def clusters(self, level: str) -> dict[int, list[NodeId]]:
        out: dict[int, list[NodeId]] = {}
        for n, c in self.labels[level].items():
            out.setdefault(c, []).append(n)
        return out

    def path(self, node: NodeId) -> str:
        """Hierarchical label such as ``continent-1/country-4/city-9/datacenter-17``."""
        parts = [f"{lv.name}-{self.labels[lv.name][node]}" for lv in reversed(self.levels)]
        return "/".join(parts)


def core_distance(dist: Distance, coords: Mapping[NodeId, Coordinate] | None) -> Distance:
    """Subtract both nodes' heights (access delay) from a distance function."""
    if coords is None:
        return dist

    def core(a: NodeId, b: NodeId) -> float | None:
        v = dist(a, b)
        if v is None:
            return None
        ha = coords[a].height if a in coords else 0.0
        hb = coords[b].height if b in coords else 0.0
        return max(0.0, v - ha - hb)

    return core


def cluster_hierarchy(
    nodes: Sequence[NodeId],
    dist: Distance,
    levels: Sequence[Level] = DEFAULT_LEVELS,
    coords: Mapping[NodeId, Coordinate] | None = None,
) -> Clustering:
    ordered = list(levels)
    if [lv.max_rtt_ms for lv in ordered] != sorted(lv.max_rtt_ms for lv in ordered):
        raise ValueError("levels must be ordered by increasing threshold")
    d = core_distance(dist, coords)
    n = len(nodes)
    base = np.full((n, n), np.inf)
    for i in range(n):
        base[i, i] = 0.0
        for j in range(i + 1, n):
            v = d(nodes[i], nodes[j])
            if v is not None:
                base[i, j] = base[j, i] = v

    result = Clustering(tuple(ordered))
    groups: list[list[int]] = [[i] for i in range(n)]  # clusters as lists of node indices
    for lv in ordered:
        m = len(groups)
        gd = np.full((m, m), np.inf)
        for a in range(m):
            gd[a, a] = 0.0
            for b in range(a + 1, m):
                block = base[np.ix_(groups[a], groups[b])]
                finite = block[np.isfinite(block)]
                if finite.size:
                    gd[a, b] = gd[b, a] = float(finite.mean())
        merged = average_linkage(gd, lv.max_rtt_ms)
        groups = [sorted(i for g in grp for i in groups[g]) for grp in merged]
        result.labels[lv.name] = {nodes[i]: c for c, grp in enumerate(groups) for i in grp}
    return result


@dataclass(frozen=True, slots=True)
class PairScore:
    precision: float  # of pairs put together, share that truly belong together
    recall: float  # of pairs that truly belong together, share put together


def pair_score(
    clustering: Clustering,
    level: str,
    truth: Callable[[NodeId], object],
    nodes: Sequence[NodeId] | None = None,
) -> PairScore:
    """Pairwise precision/recall of one level against a ground-truth labelling."""
    ids = list(nodes) if nodes is not None else list(clustering.labels[level])
    tp = fp = fn = 0
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            pred = clustering.same(level, a, b)
            real = truth(a) == truth(b)
            tp += pred and real
            fp += pred and not real
            fn += real and not pred
    return PairScore(tp / (tp + fp) if tp + fp else 1.0, tp / (tp + fn) if tp + fn else 1.0)
