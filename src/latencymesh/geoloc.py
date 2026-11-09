"""Constraint-Based Geolocation (Gueye, Ziviani, Crovella & Fdida, ToN 2006).

Every landmark with a known location turns an RTT into a disk: the target must be
within ``radius(rtt)`` km of it. The intersection of all disks is the feasible region;
its centroid is the location estimate. Radii come from a per-landmark **bestline** -
the line ``rtt = m·d + b`` that lies below all of the landmark's (distance, RTT) points
to other landmarks, as close to them as possible, with slope no smaller than the
speed of light in fibre allows. An empty intersection proves that some measurement or
claim is false.

RTT bounds distance only from above, so a node can always *pretend to be farther*
by delaying replies. :func:`verify_claim` therefore checks a claimed location in both
directions: no landmark may be closer in RTT than physics allows for the claimed
distance (hard), and no landmark may be much farther in RTT than paths of that
length plausibly are (soft, calibrated on the genesis).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from .genesis import Genesis
from .geo import EARTH_RADIUS_KM, KM_PER_MS_RTT
from .model import Location, NodeId

PHYS_SLOPE = 1.0 / KM_PER_MS_RTT  # ms of RTT per km, the steepest-allowed lower bound


@dataclass(frozen=True, slots=True)
class Bestline:
    slope: float  # ms per km
    intercept: float  # ms

    def radius_km(self, rtt_ms: float) -> float:
        return max(0.0, (rtt_ms - self.intercept) / self.slope)


def fit_bestline(distances_km: Sequence[float], rtts_ms: Sequence[float]) -> Bestline:
    """Line below every point, slope >= physical slope, minimal total vertical gap."""
    d = np.asarray(distances_km, dtype=float)
    r = np.asarray(rtts_ms, dtype=float)
    if d.size == 0:
        return Bestline(PHYS_SLOPE, 0.0)
    candidates: list[tuple[float, float]] = []
    # physical slope through the most constraining point
    candidates.append((PHYS_SLOPE, float(np.min(r - PHYS_SLOPE * d))))
    # edges of the lower convex hull with admissible slope
    hull = _lower_hull(sorted(zip(d.tolist(), r.tolist(), strict=True)))
    for (x1, y1), (x2, y2) in itertools.pairwise(hull):
        if x2 > x1:
            m = (y2 - y1) / (x2 - x1)
            if m >= PHYS_SLOPE:
                candidates.append((m, y1 - m * x1))
    best = min(
        (c for c in candidates if np.all(r - (c[0] * d + c[1]) >= -1e-9)),
        key=lambda c: float(np.sum(r - (c[0] * d + c[1]))),
    )
    return Bestline(best[0], min(best[1], float(np.min(r))))


def _lower_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    hull: list[tuple[float, float]] = []
    for p in points:
        while len(hull) >= 2:
            (x1, y1), (x2, y2) = hull[-2], hull[-1]
            if (x2 - x1) * (p[1] - y1) - (y2 - y1) * (p[0] - x1) <= 0:
                hull.pop()
            else:
                break
        hull.append(p)
    return hull


@dataclass(frozen=True, slots=True)
class Landmark:
    id: NodeId
    location: Location
    bestline: Bestline
    #: 95th percentile of (rtt − line) / line to other landmarks: plausible relative inflation
    slack_ratio: float


def calibrate(genesis: Genesis) -> dict[NodeId, Landmark]:
    """Fit a bestline and an inflation allowance for every trusted node."""
    locs = {n.id: n.location for n in genesis.nodes if n.location is not None}
    out: dict[NodeId, Landmark] = {}
    for lid, loc in locs.items():
        pts = [
            (loc.distance_km(locs[o]), genesis.rtt[(min(lid, o), max(lid, o))])
            for o in locs
            if o != lid
        ]
        bl = fit_bestline([p[0] for p in pts], [p[1] for p in pts])
        rel = [(rt - line) / line for dk, rt in pts if (line := bl.slope * dk + bl.intercept) > 1.0]
        slack = float(np.percentile(rel, 95)) if rel else 1.0
        out[lid] = Landmark(lid, loc, bl, slack)
    return out


@dataclass(frozen=True, slots=True)
class GeoEstimate:
    lat: float
    lon: float
    #: False only when even the speed-of-light disks do not intersect: proof of a lie
    feasible: bool
    #: "bestline" (tight, calibrated) or "physical" (sound fallback) radii were used
    method: str
    #: approximate radius (km) of the feasible region around the estimate
    uncertainty_km: float
    used: int


def _unit(lat: npt.NDArray[np.float64], lon: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    la, lo = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)


def _grid_dist(
    lat: npt.NDArray[np.float64], lon: npt.NDArray[np.float64], marks: Sequence[Landmark]
) -> npt.NDArray[np.float64]:
    g = _unit(lat, lon)
    m = _unit(
        np.array([lm.location.lat for lm in marks]), np.array([lm.location.lon for lm in marks])
    )
    cos = np.clip(g @ m.T, -1.0, 1.0)
    return np.asarray(np.arccos(cos) * EARTH_RADIUS_KM, dtype=np.float64)


def locate(
    rtts: Mapping[NodeId, float], landmarks: Mapping[NodeId, Landmark], step_deg: float = 1.0
) -> GeoEstimate:
    """CBG estimate. Bestline radii are tight but can exclude the true position when a
    target has a better route than any landmark pair; then the sound speed-of-light
    radii are used instead."""
    marks = [landmarks[k] for k in rtts if k in landmarks]
    if not marks:
        raise ValueError("no RTTs to known landmarks")
    tight = np.array([lm.bestline.radius_km(rtts[lm.id]) for lm in marks])
    sound = np.array([rtts[lm.id] * KM_PER_MS_RTT for lm in marks])
    lat, lon = _grid(step_deg)
    dist = _grid_dist(lat, lon, marks)
    pad = _cell_pad_km(step_deg)  # a disk smaller than a grid cell must still hit a point
    for method, radii in (("bestline", tight), ("physical", sound)):
        violation = np.max(dist - radii - pad, axis=1)
        if (violation <= 0).any():
            return _estimate(lat, lon, violation <= 0, radii, marks, step_deg, method, True)
    best = violation <= violation.min() + 1e-6  # least-violating points, physical radii
    return _estimate(lat, lon, best, sound, marks, step_deg, "physical", False)


def _estimate(
    lat: npt.NDArray[np.float64],
    lon: npt.NDArray[np.float64],
    ok: npt.NDArray[np.bool_],
    radii: npt.NDArray[np.float64],
    marks: Sequence[Landmark],
    step_deg: float,
    method: str,
    feasible: bool,
) -> GeoEstimate:
    if feasible:  # refine on a finer grid around the feasible area
        la0, la1 = float(np.min(lat[ok])) - step_deg, float(np.max(lat[ok])) + step_deg
        lo0, lo1 = float(np.min(lon[ok])) - step_deg, float(np.max(lon[ok])) + step_deg
        fl, fo = _grid(step_deg / 4, (la0, la1, lo0, lo1))
        fd = _grid_dist(fl, fo, marks)
        fok = np.max(fd - radii - _cell_pad_km(step_deg / 4), axis=1) <= 0
        if fok.any():
            lat, lon, ok = fl, fo, fok
    pts = _unit(lat[ok], lon[ok])
    c = pts.mean(axis=0)
    c /= np.linalg.norm(c)
    spread = float(np.arccos(np.clip(pts @ c, -1, 1)).max() * EARTH_RADIUS_KM)
    lat_c = float(np.degrees(np.arcsin(c[2])))
    lon_c = float(np.degrees(np.arctan2(c[1], c[0])))
    return GeoEstimate(lat_c, lon_c, feasible, method, spread, len(marks))


def _cell_pad_km(step_deg: float) -> float:
    """Half-diagonal of a grid cell at the equator (worst case)."""
    return step_deg * 111.32 * math.sqrt(2) / 2


def _grid(
    step: float, box: tuple[float, float, float, float] = (-90, 90, -180, 180)
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    la0, la1, lo0, lo1 = box
    lats = np.arange(max(-90.0, la0), min(90.0, la1) + 1e-9, step)
    lons = np.arange(max(-180.0, lo0), min(180.0, lo1) + 1e-9, step)
    gl, go = np.meshgrid(lats, lons, indexing="ij")
    return gl.ravel(), go.ravel()


@dataclass(frozen=True, slots=True)
class ClaimCheck:
    ok: bool
    #: landmarks whose RTT is impossible (faster than light) for the claimed location
    too_fast: list[NodeId]
    #: landmarks whose RTT is implausibly slow for the claimed distance
    too_slow: list[NodeId]


def verify_claim(
    claimed: Location,
    rtts: Mapping[NodeId, float],
    landmarks: Mapping[NodeId, Landmark],
    slack_factor: float = 1.5,
    min_slack_ms: float = 5.0,
    max_too_slow_share: float = 0.2,
) -> ClaimCheck:
    """Check a claimed location against measured RTTs from landmarks.

    *too fast* (RTT below the physical bound for the claimed distance) is a proof of a
    false claim. *too slow* is statistical. A delay common to all landmarks (the node's
    access link, or a uniform artificial delay) is estimated as the smallest excess over
    the bestlines and removed first; what remains must fit within the calibrated
    relative inflation. Legitimately bad routes exist, so the claim fails only when more
    than ``max_too_slow_share`` of landmarks are too slow.
    """
    rows = []
    for lid, rtt in rtts.items():
        lm = landmarks.get(lid)
        if lm is None:
            continue
        dkm = claimed.distance_km(lm.location)
        rows.append((lid, rtt, dkm, lm.bestline.slope * dkm + lm.bestline.intercept, lm))
    too_fast = [lid for lid, rtt, dkm, _, _ in rows if rtt < dkm * PHYS_SLOPE * 0.98]
    common = max(0.0, min((rtt - exp for _, rtt, _, exp, _ in rows), default=0.0))
    too_slow = [
        lid
        for lid, rtt, _, exp, lm in rows
        if rtt - common - exp > max(min_slack_ms, slack_factor * lm.slack_ratio * exp)
    ]
    ok = not too_fast and (not rows or len(too_slow) / len(rows) <= max_too_slow_share)
    return ClaimCheck(ok, too_fast, too_slow)
