"""Discrete-event simulation that keeps the RTT graph up to date.

``Simulation`` ties together the ground truth (:class:`~latencymesh.synth.World`) and
what the network *knows* (:class:`~latencymesh.graph.RTTGraph`). On every tick a
:class:`ProbePolicy` picks which pairs to measure within a per-node probe budget; each
probe is a burst of pings whose minimum becomes a :class:`~latencymesh.model.Measurement`.

Two hooks make adversaries easy to express:

* ``World.added_delay_ms`` – a node delays its replies (physically realistic attack),
* ``Simulation.reporters`` – a node *reports* something other than what it measured
  (lying or lazy observers); return ``None`` to drop the report.

Listeners registered with :meth:`Simulation.on_measurement` see every accepted
measurement – coordinate systems such as Vivaldi plug in there.
"""

from __future__ import annotations

import heapq
import itertools
import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from .graph import RTTGraph
from .model import Measurement, NodeId
from .synth import World

Reporter = Callable[[Measurement], Measurement | None]
Listener = Callable[[Measurement], None]
Probe = tuple[NodeId, NodeId]  # (observer, target)


class EventLoop:
    """Minimal deterministic event loop (time in seconds, ties broken by insertion order)."""

    def __init__(self) -> None:
        self.now = 0.0
        self._queue: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = itertools.count()

    def schedule(self, delay: float, fn: Callable[[], None]) -> None:
        if delay < 0:
            raise ValueError("cannot schedule in the past")
        heapq.heappush(self._queue, (self.now + delay, next(self._seq), fn))

    def every(self, interval: float, fn: Callable[[], None], start: float = 0.0) -> None:
        def tick() -> None:
            fn()
            self.schedule(interval, tick)

        self.schedule(start, tick)

    def run(self, until: float) -> None:
        while self._queue and self._queue[0][0] <= until:
            t, _, fn = heapq.heappop(self._queue)
            self.now = t
            fn()
        self.now = max(self.now, until)

    def pending(self) -> int:
        return len(self._queue)


class ProbePolicy(Protocol):
    """Chooses which (observer, target) probes to run in a tick."""

    def plan(self, sim: Simulation, budget: int, rng: random.Random) -> list[Probe]: ...


@dataclass(slots=True)
class RandomPairs:
    """Every node probes ``budget`` uniformly random other nodes (exploration)."""

    def plan(self, sim: Simulation, budget: int, rng: random.Random) -> list[Probe]:
        ids = sim.graph.node_ids
        out: list[Probe] = []
        for a in ids:
            picks = [b for b in rng.sample(ids, min(budget + 1, len(ids))) if b != a]
            out.extend((a, b) for b in picks[:budget])
        return out


@dataclass(slots=True)
class RefreshAndExplore:
    """Re-measure each node's oldest known edges, spend the rest of the budget exploring.

    ``explore_share`` of the budget goes to random new targets so that the graph keeps
    growing; the remainder refreshes the stalest existing edges so the estimates follow
    routing changes. This is the default "continuous re-measurement" policy.
    """

    explore_share: float = 0.3

    def plan(self, sim: Simulation, budget: int, rng: random.Random) -> list[Probe]:
        g = sim.graph
        ids = g.node_ids
        out: list[Probe] = []
        n_explore = max(1, round(budget * self.explore_share)) if budget else 0
        n_refresh = budget - n_explore
        for a in ids:
            known = g.neighbors(a)
            if n_refresh and known:
                by_age = sorted(known, key=lambda b: _last_time(g, a, b))
                out.extend((a, b) for b in by_age[:n_refresh])
            candidates = rng.sample(ids, min(len(ids), n_explore * 3 + 1))
            fresh = [b for b in candidates if b != a and b not in known][:n_explore]
            out.extend((a, b) for b in fresh)
        return out


def _last_time(g: RTTGraph, a: NodeId, b: NodeId) -> float:
    e = g.edge(a, b)
    return e.last_time if e else float("-inf")


@dataclass(slots=True)
class SimStats:
    probes: int = 0
    accepted: int = 0
    dropped: int = 0
    ticks: int = 0


@dataclass
class Simulation:
    world: World
    graph: RTTGraph
    seed: int = 0
    pings_per_probe: int = 5
    probe_budget: int = 4
    tick_interval: float = 60.0
    policy: ProbePolicy = field(default_factory=RefreshAndExplore)
    reporters: dict[NodeId, Reporter] = field(default_factory=dict)
    loop: EventLoop = field(default_factory=EventLoop)
    stats: SimStats = field(default_factory=SimStats)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        self._listeners: list[Listener] = []
        for nid in self.world.nodes:
            if nid not in self.graph:
                raise ValueError(f"graph is missing world node {nid!r}")

    @classmethod
    def from_genesis(cls, world: World, *, include_all: bool = True, **kw: object) -> Simulation:
        """Start from the world's genesis; optionally add the non-trusted nodes unmeasured."""
        graph = world.genesis().to_graph()
        if include_all:
            for n in world.nodes.values():
                if n.id not in graph:
                    graph.add_node(n)
        return cls(world=world, graph=graph, **kw)  # type: ignore[arg-type]

    @property
    def now(self) -> float:
        return self.loop.now

    def on_measurement(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def join(self, node_id: NodeId) -> None:
        """Make a node that already exists in the world part of the measured graph."""
        if node_id not in self.graph:
            self.graph.add_node(self.world.nodes[node_id])

    def measure(self, observer: NodeId, target: NodeId) -> Measurement | None:
        """Run one probe burst and record the (possibly falsified) report."""
        self.stats.probes += 1
        rtt = self.world.ping(observer, target, self.rng, self.pings_per_probe)
        m = Measurement(observer, target, rtt, time=self.now, samples=self.pings_per_probe)
        reporter = self.reporters.get(observer)
        reported = reporter(m) if reporter else m
        if reported is None:
            self.stats.dropped += 1
            return None
        self.graph.record(reported)
        self.stats.accepted += 1
        for fn in self._listeners:
            fn(reported)
        return reported

    def measure_both(self, a: NodeId, b: NodeId) -> tuple[Measurement | None, Measurement | None]:
        return self.measure(a, b), self.measure(b, a)

    def measure_many(self, probes: Iterable[Probe]) -> list[Measurement]:
        return [m for a, b in probes if (m := self.measure(a, b)) is not None]

    def tick(self) -> None:
        self.stats.ticks += 1
        self.measure_many(self.policy.plan(self, self.probe_budget, self.rng))

    def run(self, duration: float) -> None:
        """Advance the simulation, probing every ``tick_interval`` seconds."""
        if not getattr(self, "_started", False):
            self.loop.every(self.tick_interval, self.tick)
            self._started = True
        self.loop.run(self.now + duration)
