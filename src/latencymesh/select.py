"""(Partly) random node selection strategies.

All strategies take their randomness from a :class:`Beacon`, a stand-in for public,
unbiasable randomness (drand, RANDAO, a VRF output). Deriving every draw from
``(seed, round, label)`` keeps simulations reproducible and mirrors how an on-chain
protocol would select witnesses only *after* a participant has committed.

Strategies
----------
* :func:`uniform` – plain sampling without replacement.
* :func:`balanced` – power-of-*d*-choices against a :class:`SelectionLedger`, so the
  long-run selection count of every node stays close to the mean
  (max deviation O(log log n) instead of O(log n); Azar et al. 1999).
* :func:`hilbert_systematic` – order nodes along a Hilbert curve over their network
  coordinates and take one node from each of *k* equal-count segments. Every node has
  inclusion probability exactly *k/n* per draw, while the sample is spread over the
  coordinate space *proportionally to node density*.
* :func:`dispersed` – randomised greedy farthest-point (max-min) sampling: maximises
  spread regardless of density, at the cost of per-node uniformity.
* :func:`around` / :func:`k_nearest` – nodes near a centre.
* :func:`iterative_nearest` – multi-phase nearest-neighbour discovery for a joining node
  (Meridian-style narrowing, Wong et al. 2005).
* :func:`swap_or_not_index` / :class:`EpochPermutation` – the swap-or-not shuffle
  (Hoang, Morris & Rogaway 2012) as used for committee selection in Ethereum: every node
  is selected exactly once per epoch without storing the permutation.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from .coords.base import Coordinate
from .graph import RTTGraph
from .model import NodeId

Distance = Callable[[NodeId, NodeId], float | None]


# -- randomness -----------------------------------------------------------------
@dataclass(slots=True)
class Beacon:
    """Deterministic stand-in for a public randomness beacon."""

    seed: int = 0
    round: int = 0

    def value(self, label: str = "") -> bytes:
        return hashlib.sha256(f"{self.seed}|{self.round}|{label}".encode()).digest()

    def rng(self, label: str = "") -> random.Random:
        return random.Random(int.from_bytes(self.value(label), "big"))

    def next_round(self) -> int:
        self.round += 1
        return self.round


# -- bookkeeping ----------------------------------------------------------------
@dataclass(slots=True)
class SelectionLedger:
    """How often each node was selected. New nodes start at the current mean so they
    are not over-selected while "catching up"."""

    counts: dict[NodeId, float] = field(default_factory=dict)

    def ensure(self, nodes: Sequence[NodeId]) -> None:
        missing = [n for n in nodes if n not in self.counts]
        if missing:
            mean = sum(self.counts.values()) / len(self.counts) if self.counts else 0.0
            for n in missing:
                self.counts[n] = mean

    def record(self, nodes: Sequence[NodeId]) -> None:
        self.ensure(nodes)
        for n in nodes:
            self.counts[n] += 1

    def spread(self, nodes: Sequence[NodeId] | None = None) -> float:
        vals = [self.counts[n] for n in (nodes or list(self.counts))]
        return (max(vals) - min(vals)) if vals else 0.0


def make_distance(
    graph: RTTGraph | None = None, coords: Mapping[NodeId, Coordinate] | None = None
) -> Distance:
    """Measured RTT when the graph has the edge, otherwise coordinate prediction."""

    def dist(a: NodeId, b: NodeId) -> float | None:
        if graph is not None:
            v = graph.rtt(a, b) if a in graph and b in graph else None
            if v is not None:
                return v
        if coords is not None and a in coords and b in coords:
            return coords[a].distance(coords[b])
        return None

    return dist


def mean_pairwise(nodes: Sequence[NodeId], dist: Distance) -> float:
    vals = [d for i, a in enumerate(nodes) for b in nodes[i + 1 :] if (d := dist(a, b)) is not None]
    return float(np.mean(vals)) if vals else 0.0


# -- strategies -----------------------------------------------------------------
def uniform(candidates: Sequence[NodeId], k: int, rng: random.Random) -> list[NodeId]:
    _check_k(candidates, k)
    return rng.sample(list(candidates), k)


def balanced(
    candidates: Sequence[NodeId],
    k: int,
    rng: random.Random,
    ledger: SelectionLedger,
    choices: int = 2,
    record: bool = True,
) -> list[NodeId]:
    """Each slot draws ``choices`` candidates and keeps the least-selected one."""
    _check_k(candidates, k)
    ledger.ensure(candidates)
    pool = list(candidates)
    out: list[NodeId] = []
    for _ in range(k):
        picks = rng.sample(pool, min(choices, len(pool)))
        best = min(picks, key=lambda n: (ledger.counts[n], rng.random()))
        out.append(best)
        pool.remove(best)
    if record:
        ledger.record(out)
    return out


def hilbert_order(coords: Mapping[NodeId, Coordinate], bits: int = 16) -> list[NodeId]:
    """Nodes sorted by the Hilbert index of their coordinates' 2-D principal projection."""
    ids = sorted(coords)
    if len(ids) < 2:
        return ids
    x = np.array([coords[i].vec for i in ids])
    x = x - x.mean(axis=0)
    _, _, vt = np.linalg.svd(x, full_matrices=False)
    p = x @ vt[: min(2, vt.shape[0])].T
    if p.shape[1] == 1:
        p = np.hstack([p, np.zeros_like(p)])
    lo, hi = p.min(axis=0), p.max(axis=0)
    side = (1 << bits) - 1
    grid = np.round((p - lo) / np.where(hi > lo, hi - lo, 1.0) * side).astype(int)
    keys = [_hilbert_d(bits, int(gx), int(gy)) for gx, gy in grid]
    return [n for _, n in sorted(zip(keys, ids, strict=True))]


def hilbert_systematic(
    order: Sequence[NodeId],
    k: int,
    rng: random.Random,
    ledger: SelectionLedger | None = None,
    choices: int = 1,
) -> list[NodeId]:
    """Systematic sample along a precomputed Hilbert order (see :func:`hilbert_order`).

    With ``choices > 1`` and a ledger, each segment is sampled ``choices`` times and
    the least-selected node wins - combining density-proportional spread with
    power-of-d balancing.
    """
    _check_k(order, k)
    n = len(order)
    if ledger is not None:
        ledger.ensure(order)
    out: list[NodeId] = []
    for i in range(k):
        start, end = (i * n) // k, ((i + 1) * n) // k
        picks = [order[rng.randrange(start, end)] for _ in range(max(1, choices))]
        if ledger is not None:
            picks.sort(key=lambda m: ledger.counts[m])
        out.append(picks[0])
    if ledger is not None:
        ledger.record(out)
    return out


def dispersed(
    candidates: Sequence[NodeId],
    k: int,
    dist: Distance,
    rng: random.Random,
    top: int = 3,
) -> list[NodeId]:
    """Randomised farthest-point sampling.

    Start from a random node; repeatedly add one of the ``top`` candidates with the
    largest distance to the already chosen set (``top=1`` is the deterministic greedy
    2-approximation of max-min dispersion; larger values trade spread for randomness).
    """
    _check_k(candidates, k)
    pool = list(candidates)
    first = rng.choice(pool)
    chosen = [first]
    pool.remove(first)
    mind = {c: _d(dist, c, first) for c in pool}
    while len(chosen) < k:
        ranked = sorted(pool, key=lambda c: mind[c], reverse=True)[: max(1, top)]
        nxt = rng.choice(ranked)
        chosen.append(nxt)
        pool.remove(nxt)
        del mind[nxt]
        for c in pool:
            mind[c] = min(mind[c], _d(dist, c, nxt))
    return chosen


def k_nearest(
    center: NodeId, candidates: Sequence[NodeId], k: int, dist: Distance
) -> list[tuple[NodeId, float]]:
    scored = [(c, d) for c in candidates if c != center and (d := dist(center, c)) is not None]
    return sorted(scored, key=lambda t: (t[1], t[0]))[:k]


def around(
    center: NodeId,
    candidates: Sequence[NodeId],
    k: int,
    dist: Distance,
    rng: random.Random,
    radius_ms: float | None = None,
    pool_factor: int = 4,
) -> list[NodeId]:
    """Random nodes close to ``center``: uniform among those within ``radius_ms`` or,
    without a radius, among the ``k * pool_factor`` nearest."""
    if radius_ms is not None:
        pool = [c for c in candidates if c != center and (_d(dist, center, c) <= radius_ms)]
    else:
        pool = [c for c, _ in k_nearest(center, candidates, k * pool_factor, dist)]
    return rng.sample(pool, min(k, len(pool)))


@dataclass(slots=True)
class NearestSearch:
    nearest: list[tuple[NodeId, float]]
    probes: int
    phases: list[list[NodeId]]


def iterative_nearest(
    probe: Callable[[NodeId], float],
    candidates: Sequence[NodeId],
    dist: Distance,
    rng: random.Random,
    x: int = 10,
    t: int = 3,
    max_phases: int = 6,
    patience: int = 2,
    coords: Mapping[NodeId, Coordinate] | None = None,
) -> NearestSearch:
    """Find the ``t`` nearest existing nodes to a newcomer by probing only a few of them.

    * Phase 1 probes ``x`` dispersed nodes.
    * Every next phase builds a pool with the triangle-inequality filter used by
      Meridian: a node closer to the newcomer than ``d = rtt(newcomer, anchor)`` must lie
      within ``2·d`` of the anchor, for each of the current ``t`` best anchors.
    * With ``coords``, the newcomer's coordinate is estimated from all RTTs measured so
      far (:func:`~latencymesh.coords.fit_position`); half of each batch goes to the
      pool members predicted closest, half is random exploration.
    * Stops after ``patience`` phases without improving the best ``t`` set.

    ``probe(node)`` returns the measured RTT between ``node`` and the newcomer.
    """
    from .coords.gnp import fit_position

    measured: dict[NodeId, float] = {}
    phases: list[list[NodeId]] = []
    first = dispersed(candidates, min(x, len(candidates)), dist, rng)
    for c in first:
        measured[c] = probe(c)
    phases.append(first)

    def best() -> list[tuple[NodeId, float]]:
        return sorted(measured.items(), key=lambda kv: (kv[1], kv[0]))[:t]

    stale = 0
    for _ in range(max_phases - 1):
        current = best()
        pool = {
            c
            for anchor, d in current
            for c in candidates
            if c not in measured and _d(dist, anchor, c) <= 2 * d + 1.0
        }
        if not pool:
            break
        ordered = sorted(pool)
        batch: list[NodeId] = []
        if coords is not None:
            refs = [n for n in measured if n in coords]
            if len(refs) > next(iter(coords.values())).dims + 1:
                me = fit_position([coords[n] for n in refs], [measured[n] for n in refs])
                ranked = sorted(
                    (c for c in ordered if c in coords), key=lambda c: me.distance(coords[c])
                )
                batch = ranked[: x // 2]
        rest = [c for c in ordered if c not in batch]
        batch += rng.sample(rest, min(x - len(batch), len(rest)))
        for c in batch:
            measured[c] = probe(c)
        phases.append(batch)
        if [n for n, _ in best()] == [n for n, _ in current]:
            stale += 1
            if stale >= patience:
                break
        else:
            stale = 0
    return NearestSearch(best(), len(measured), phases)


# -- swap-or-not ----------------------------------------------------------------
def swap_or_not_index(index: int, n: int, seed: bytes, rounds: int = 90) -> int:
    """Position of ``index`` in a pseudo-random permutation of ``range(n)``.

    Same construction as Ethereum's ``compute_shuffled_index``: O(rounds) per lookup,
    no stored permutation, so any slice of an epoch's ordering is cheap to compute both
    off- and on-chain.
    """
    if not 0 <= index < n:
        raise ValueError("index out of range")
    for r in range(rounds):
        rb = r.to_bytes(1, "little")
        pivot = int.from_bytes(hashlib.sha256(seed + rb).digest()[:8], "little") % n
        flip = (pivot + n - index) % n
        position = max(index, flip)
        source = hashlib.sha256(seed + rb + (position // 256).to_bytes(4, "little")).digest()
        bit = (source[(position % 256) // 8] >> (position % 8)) & 1
        if bit:
            index = flip
    return index


@dataclass(slots=True)
class EpochPermutation:
    """Walk a fresh permutation per epoch with a cursor: every node is chosen exactly
    once per ``len(nodes)/k`` draws. Predictable within an epoch once the seed is
    public - use it where that is acceptable (e.g. audit scheduling)."""

    nodes: Sequence[NodeId]
    seed: bytes
    cursor: int = 0
    epoch: int = 0

    def draw(self, k: int) -> list[NodeId]:
        n = len(self.nodes)
        out: list[NodeId] = []
        for _ in range(k):
            if self.cursor == n:
                self.cursor = 0
                self.epoch += 1
            epoch_seed = hashlib.sha256(self.seed + self.epoch.to_bytes(8, "little")).digest()
            out.append(self.nodes[swap_or_not_index(self.cursor, n, epoch_seed)])
            self.cursor += 1
        return out


# -- helpers ----------------------------------------------------------------------
def _check_k(candidates: Sequence[NodeId], k: int) -> None:
    if not 0 < k <= len(candidates):
        raise ValueError(f"cannot select {k} of {len(candidates)} nodes")


def _d(dist: Distance, a: NodeId, b: NodeId) -> float:
    v = dist(a, b)
    return float("inf") if v is None else v


def _hilbert_d(bits: int, x: int, y: int) -> int:
    """Index along a Hilbert curve of order ``bits`` (classic xy→d algorithm)."""
    n = 1 << bits
    d = 0
    s = n >> 1
    while s > 0:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x = n - 1 - x
                y = n - 1 - y
            x, y = y, x
        s >>= 1
    return d
