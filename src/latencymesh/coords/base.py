"""Shared coordinate representation: Euclidean vector plus a non-negative *height*.

Predicted RTT between two coordinates is ``‖x_a − x_b‖ + h_a + h_b``. The height models
the access link: traffic has to climb it on both ends regardless of direction
(Dabek et al., *Vivaldi*, 2004). With all heights at zero this is plain GNP space.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from ..model import NodeId

Vector = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class Coordinate:
    vec: Vector
    height: float = 0.0

    @property
    def dims(self) -> int:
        return int(self.vec.shape[0])

    def distance(self, other: Coordinate) -> float:
        return float(np.linalg.norm(self.vec - other.vec)) + self.height + other.height


def relative_errors(
    predict: Callable[[NodeId, NodeId], float | None],
    truth: Callable[[NodeId, NodeId], float],
    pairs: Iterable[tuple[NodeId, NodeId]],
) -> list[float]:
    """|predicted − true| / true for every pair that has a prediction."""
    out = []
    for a, b in pairs:
        p = predict(a, b)
        t = truth(a, b)
        if p is not None and t > 0:
            out.append(abs(p - t) / t)
    return out
