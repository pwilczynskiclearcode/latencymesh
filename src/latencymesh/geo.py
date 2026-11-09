"""Geographic helpers and the speed-of-light bound on RTT.

Light in optical fibre travels at roughly 2/3 of *c*, i.e. ~200 km per millisecond one-way.
A round trip therefore covers at most ~100 km of great-circle distance per millisecond of RTT.
Real paths are longer than the great circle, so measured RTTs are always *above* this bound;
an RTT *below* it is physically impossible and proves that a measurement (or a claimed location)
is false.
"""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088
SPEED_OF_LIGHT_KM_PER_MS = 299.792458
FIBRE_VELOCITY_FACTOR = 2.0 / 3.0
#: Great-circle kilometres covered per millisecond of *round-trip* time in fibre.
KM_PER_MS_RTT = SPEED_OF_LIGHT_KM_PER_MS * FIBRE_VELOCITY_FACTOR / 2.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points given in decimal degrees."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def min_rtt_ms(distance_km: float) -> float:
    """Smallest physically possible RTT over fibre for a great-circle distance."""
    return distance_km / KM_PER_MS_RTT


def max_distance_km(rtt_ms: float) -> float:
    """Largest great-circle distance compatible with a measured RTT."""
    return max(0.0, rtt_ms) * KM_PER_MS_RTT
