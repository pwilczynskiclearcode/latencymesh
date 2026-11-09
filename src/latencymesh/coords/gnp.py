"""GNP - Global Network Positioning (Ng & Zhang, INFOCOM 2002).

Two steps:

1. **Landmarks** (here: the trusted genesis nodes, whose full RTT matrix is known) are
   embedded jointly by minimising the squared *relative* error between coordinate
   distance and measured RTT.
2. Any other **host** measures RTT to a subset of landmarks and solves the same
   problem for its own coordinate only, with landmark coordinates fixed.

The original paper used Simplex Downhill and no heights. Here the landmark embedding is
initialised with classical multidimensional scaling (Torgerson) - random starts get
stuck because the relative-error objective is dominated by sub-millisecond pairs - and
then refined with Adam on the analytic gradient, optionally fitting heights.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from ..model import NodeId
from .base import Coordinate, Vector

Matrix = npt.NDArray[np.float64]


def _adam(
    x: Matrix,
    grad_fn: Callable[[Matrix], Matrix],
    steps: int,
    lr: float,
    project: Callable[[Matrix], Matrix] | None = None,
) -> Matrix:
    """Adam; ``project`` maps iterates back to the feasible set (e.g. heights >= 0)."""
    m = np.zeros_like(x)
    v = np.zeros_like(x)
    b1, b2, eps = 0.9, 0.999, 1e-8
    for t in range(1, steps + 1):
        g = grad_fn(x)
        m = b1 * m + (1 - b1) * g
        v = b2 * v + (1 - b2) * g * g
        x = x - lr * (m / (1 - b1**t)) / (np.sqrt(v / (1 - b2**t)) + eps)
        if project is not None:
            x = project(x)
    return x


def _nonneg_last(x: Matrix) -> Matrix:
    x[..., -1] = np.maximum(x[..., -1], 0.0)
    return x


IndexArray = npt.NDArray[np.intp]


def classical_mds(rtt: Matrix, dims: int) -> Matrix:
    """Torgerson MDS: coordinates whose Euclidean distances best match ``rtt`` (in L2)."""
    n = rtt.shape[0]
    j = np.eye(n) - np.ones((n, n)) / n
    b = -0.5 * j @ (rtt**2) @ j
    vals, vecs = np.linalg.eigh(b)
    order = np.argsort(vals)[::-1][:dims]
    return np.asarray(vecs[:, order] * np.sqrt(np.maximum(vals[order], 0.0)), dtype=np.float64)


def _loss_and_grad(
    pos: Matrix, h: Vector, ii: IndexArray, jj: IndexArray, meas: Vector
) -> tuple[float, Matrix, Vector]:
    """Relative-error loss over edge list (i, j, meas) and its gradient."""
    diff = pos[ii] - pos[jj]
    eucl = np.linalg.norm(diff, axis=1)
    pred = eucl + h[ii] + h[jj]
    err = (pred - meas) / meas
    coef = 2 * err / meas
    unit = diff / np.maximum(eucl, 1e-9)[:, None]
    g_pos = np.zeros_like(pos)
    np.add.at(g_pos, ii, coef[:, None] * unit)
    np.add.at(g_pos, jj, -coef[:, None] * unit)
    g_h = np.zeros_like(h)
    np.add.at(g_h, ii, coef)
    np.add.at(g_h, jj, coef)
    return float(np.sum(err**2)), g_pos, g_h


@dataclass
class GNP:
    dims: int = 5
    use_height: bool = True
    steps: int = 4000
    lr: float = 0.2
    restarts: int = 3
    seed: int = 0
    landmarks: dict[NodeId, Coordinate] = field(default_factory=dict)

    # -- step 1: landmarks -------------------------------------------------------
    def fit_landmarks(self, ids: Sequence[NodeId], rtt: Matrix) -> float:
        """Embed landmarks from their full RTT matrix; returns final mean squared rel. error."""
        n = len(ids)
        if rtt.shape != (n, n) or np.isnan(rtt).any():
            raise ValueError("landmark RTT matrix must be complete and n x n")
        iu, ju = np.triu_indices(n, 1)
        meas = rtt[iu, ju]
        rng = np.random.default_rng(self.seed)
        base = classical_mds(rtt, self.dims)
        spread = float(np.std(base)) or 1.0
        best: tuple[float, Matrix, Vector] | None = None
        for r in range(self.restarts):
            jitter = 0.0 if r == 0 else 0.05 * spread
            x0 = np.concatenate(
                [base + rng.normal(0, jitter, base.shape), np.zeros((n, 1))], axis=1
            )

            def grad(x: Matrix) -> Matrix:
                _, gp, gh = _loss_and_grad(x[:, :-1], x[:, -1], iu, ju, meas)
                if not self.use_height:
                    gh = np.zeros_like(gh)
                return np.concatenate([gp, gh[:, None]], axis=1)

            x = _adam(x0, grad, self.steps, self.lr, _nonneg_last)
            loss, _, _ = _loss_and_grad(x[:, :-1], x[:, -1], iu, ju, meas)
            if best is None or loss < best[0]:
                best = (loss, x[:, :-1].copy(), x[:, -1].copy())
        assert best is not None
        loss, pos, h = best
        self.landmarks = {
            nid: Coordinate(pos[k], float(h[k]) if self.use_height else 0.0)
            for k, nid in enumerate(ids)
        }
        return loss / len(meas)

    # -- step 2: hosts -----------------------------------------------------------
    def position(self, rtt_to_landmarks: Mapping[NodeId, float]) -> Coordinate:
        """Solve for a host coordinate from RTTs to (a subset of) the landmarks."""
        known = [(lid, v) for lid, v in rtt_to_landmarks.items() if lid in self.landmarks]
        if len(known) < self.dims + 1:
            raise ValueError(f"need at least {self.dims + 1} landmark RTTs, got {len(known)}")
        lpos = np.array([self.landmarks[lid].vec for lid, _ in known])
        lh = np.array([self.landmarks[lid].height for lid, _ in known])
        meas = np.array([v for _, v in known])
        # start from the closest landmark: good basin of attraction
        x = np.append(lpos[int(np.argmin(meas))] + 1e-3, 0.0)

        def grad(x: Vector) -> Vector:
            diff = x[:-1] - lpos
            eucl = np.linalg.norm(diff, axis=1)
            pred = eucl + lh + (x[-1] if self.use_height else 0.0)
            coef = 2 * (pred - meas) / meas**2
            gp = (coef[:, None] * diff / np.maximum(eucl, 1e-9)[:, None]).sum(0)
            gh = coef.sum() if self.use_height else 0.0
            return np.append(gp, gh)

        x = _adam(x, grad, self.steps // 4, self.lr * 2, _nonneg_last)
        return Coordinate(x[:-1], max(0.0, float(x[-1])) if self.use_height else 0.0)

    def predict(self, a: Coordinate, b: Coordinate) -> float:
        return a.distance(b)
