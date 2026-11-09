"""Genesis: the trusted starting state everyone who joins has to accept.

A genesis lists trusted nodes with authoritative locations and the RTT between
every pair of them. It is validated for completeness, symmetry and physical
plausibility (no pair may be faster than light in fibre).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .geo import min_rtt_ms
from .graph import Pair, RTTGraph, pair
from .model import Measurement, Node, NodeId

FORMAT_VERSION = 1


class GenesisError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("invalid genesis:\n  " + "\n  ".join(problems))


@dataclass(slots=True)
class Genesis:
    nodes: list[Node]
    rtt: dict[Pair, float]
    name: str = "genesis"
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        nodes: Iterable[Node],
        rtt: Mapping[tuple[NodeId, NodeId], float],
        name: str = "genesis",
    ) -> Genesis:
        """Create a genesis, canonicalising pair keys. Raises on conflicting values."""
        canon: dict[Pair, float] = {}
        for (a, b), v in rtt.items():
            k = pair(a, b)
            if k in canon and abs(canon[k] - v) > 1e-9:
                raise GenesisError([f"conflicting RTT for {k}: {canon[k]} vs {v}"])
            canon[k] = float(v)
        g = cls(nodes=list(nodes), rtt=canon, name=name)
        g.validate()
        return g

    def validate(self, fibre_tolerance: float = 0.02) -> None:
        problems: list[str] = []
        ids = [n.id for n in self.nodes]
        if len(set(ids)) != len(ids):
            problems.append("duplicate node ids")
        by_id = {n.id: n for n in self.nodes}
        for n in self.nodes:
            if not n.trusted:
                problems.append(f"{n.id}: genesis nodes must be trusted")
            if n.location is None:
                problems.append(f"{n.id}: genesis nodes need a known location")
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                if pair(a, b) not in self.rtt:
                    problems.append(f"missing RTT for {pair(a, b)}")
        for (a, b), v in self.rtt.items():
            if a not in by_id or b not in by_id:
                problems.append(f"RTT references unknown node in {(a, b)}")
                continue
            if v <= 0:
                problems.append(f"non-positive RTT for {(a, b)}: {v}")
                continue
            la, lb = by_id[a].location, by_id[b].location
            if la and lb:
                bound = min_rtt_ms(la.distance_km(lb))
                if v < bound * (1 - fibre_tolerance):
                    problems.append(
                        f"{(a, b)}: RTT {v:.2f} ms is below speed-of-light bound {bound:.2f} ms"
                    )
        if problems:
            raise GenesisError(problems)

    def to_graph(self, window: int = 8) -> RTTGraph:
        g = RTTGraph(window=window)
        for n in self.nodes:
            g.add_node(n)
        for (a, b), v in self.rtt.items():
            g.record(Measurement(a, b, v, time=0.0))
            g.record(Measurement(b, a, v, time=0.0))
        return g

    # -- serialisation -----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT_VERSION,
            "name": self.name,
            "meta": self.meta,
            "nodes": [n.to_dict() for n in self.nodes],
            "rtt": [[a, b, v] for (a, b), v in sorted(self.rtt.items())],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Genesis:
        if d.get("format") != FORMAT_VERSION:
            raise GenesisError([f"unsupported format {d.get('format')!r}"])
        g = cls(
            nodes=[Node.from_dict(n) for n in d["nodes"]],
            rtt={pair(a, b): float(v) for a, b, v in d["rtt"]},
            name=d.get("name", "genesis"),
            meta=dict(d.get("meta") or {}),
        )
        g.validate()
        return g

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False))

    @classmethod
    def load(cls, path: str | Path) -> Genesis:
        return cls.from_dict(json.loads(Path(path).read_text()))
