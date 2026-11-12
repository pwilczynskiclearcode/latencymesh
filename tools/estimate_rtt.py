"""Write a placeholder rtt.yaml for a network until real measurements exist.

RTT between two nodes is *estimated* from the great-circle distance between their
cities:  rtt_ms = 1.0 + distance_km / 100 * 1.6
(light in fibre covers about 100 km per millisecond of round trip; real routes are
longer than the great circle, hence the factor 1.6; 1 ms covers the local hops).

For every node the 5 nearest nodes are kept. Replace the file with measured values
(same format, `source: measured`) once the nodes are running.

Usage:  python tools/estimate_rtt.py src/latencymesh/networks/milky-way
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import yaml

KEEP = 5


def distance_km(a: dict[str, float], b: dict[str, float]) -> float:
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dl = math.radians(b["lon"] - a["lon"])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def main(folder: Path) -> None:
    nodes = yaml.safe_load((folder / "nodes.yaml").read_text())["nodes"]
    lines = [
        "# RTT in milliseconds from each node to its nearest nodes, closest first.",
        "# ESTIMATED from city coordinates by tools/estimate_rtt.py - not measured.",
        "source: estimated",
        "nearest:",
    ]
    for a in nodes:
        rtts = sorted(
            (round(1.0 + distance_km(a, b) / 100 * 1.6, 1), b["id"]) for b in nodes if b is not a
        )[:KEEP]
        inner = ", ".join(f"{name}: {rtt}" for rtt, name in rtts)
        lines.append(f"  {a['id']}: {{{inner}}}")
    (folder / "rtt.yaml").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
