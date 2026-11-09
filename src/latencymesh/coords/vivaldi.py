"""Vivaldi decentralised network coordinates (Dabek, Cox, Kaashoek, Morris - SIGCOMM 2004).

Each node keeps a coordinate in Euclidean space plus a height, and a local error
estimate. When node *i* measures RTT to node *j* (and learns *j*'s coordinate and error,
which in a real deployment is piggybacked on the ping reply), *i* moves along the
spring force between them, by an adaptive timestep weighted by relative confidence::

    w   = e_i / (e_i + e_j)
    e_s = | ‖x_i − x_j‖ − rtt | / rtt
    e_i = e_s·c_e·w + e_i·(1 − c_e·w)
    δ   = c_c·w
    x_i = x_i + δ·(rtt − ‖x_i − x_j‖)·u(x_i − x_j)

Height vectors follow the paper: ``[x_i,h_i] − [x_j,h_j] = [x_i − x_j, h_i + h_j]``.

*Anchors* (e.g. trusted nodes positioned by GNP) keep their coordinates fixed, which
pins the coordinate frame to the genesis and limits how far colluders can drag it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..model import Measurement, NodeId
from .base import Coordinate, Vector


@dataclass(slots=True)
class VivaldiState:
    vec: Vector
    height: float
    error: float = 1.0
    updates: int = 0

    def coordinate(self) -> Coordinate:
        return Coordinate(self.vec.copy(), self.height)


@dataclass
class Vivaldi:
    dims: int = 3
    cc: float = 0.25
    ce: float = 0.25
    min_height: float = 0.01
    seed: int = 0
    states: dict[NodeId, VivaldiState] = field(default_factory=dict)
    anchors: set[NodeId] = field(default_factory=set)

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed)

    def state(self, node: NodeId) -> VivaldiState:
        s = self.states.get(node)
        if s is None:
            s = self.states[node] = VivaldiState(np.zeros(self.dims), self.min_height)
        return s

    def anchor(self, node: NodeId, coord: Coordinate, error: float = 0.05) -> None:
        if coord.dims != self.dims:
            raise ValueError("anchor dimension mismatch")
        self.states[node] = VivaldiState(coord.vec.astype(float).copy(), coord.height, error)
        self.anchors.add(node)

    def update(self, i: NodeId, j: NodeId, rtt: float) -> None:
        if i in self.anchors or rtt <= 0:
            return
        si, sj = self.state(i), self.state(j)
        diff = si.vec - sj.vec
        eucl = float(np.linalg.norm(diff))
        if eucl < 1e-9:  # coincident: push apart in a random direction
            diff = self._rng.normal(size=self.dims)
            eucl = float(np.linalg.norm(diff))
            diff = diff / eucl * 1e-3
            eucl = 1e-3
        dist = eucl + si.height + sj.height
        w = si.error / (si.error + sj.error)
        e_s = abs(dist - rtt) / rtt
        si.error = min(1.5, e_s * self.ce * w + si.error * (1 - self.ce * w))
        delta = self.cc * w
        force = delta * (rtt - dist) / dist
        si.vec = si.vec + force * diff
        si.height = max(self.min_height, si.height + force * (si.height + sj.height))
        si.updates += 1

    def observe(self, m: Measurement) -> None:
        """Listener for :meth:`latencymesh.sim.Simulation.on_measurement`."""
        self.update(m.observer, m.target, m.rtt_ms)

    def predict(self, a: NodeId, b: NodeId) -> float | None:
        sa, sb = self.states.get(a), self.states.get(b)
        if sa is None or sb is None or (sa.updates == 0 and a not in self.anchors):
            return None
        if sb.updates == 0 and b not in self.anchors:
            return None
        return float(np.linalg.norm(sa.vec - sb.vec)) + sa.height + sb.height

    def coordinate(self, node: NodeId) -> Coordinate:
        return self.state(node).coordinate()

    def mean_error(self) -> float:
        errs = [s.error for n, s in self.states.items() if n not in self.anchors]
        return float(np.mean(errs)) if errs else math.nan
