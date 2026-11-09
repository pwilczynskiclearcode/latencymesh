"""Synthetic latency worlds used as ground truth in simulations.

The model is intentionally simple but captures what matters for proximity protocols:

* **propagation**: great-circle distance at fibre speed (see :mod:`latencymesh.geo`),
* **routing detour**: real paths are 1.2–2.5× longer than the great circle; the factor is
  drawn per pair, so it is consistent over time,
* **triangle-inequality violations (TIVs)**: a fraction of pairs gets an extra, large
  inflation (policy routing, peering disputes) which no metric embedding can represent,
* **access delay**: per-node last-mile latency (low in datacenters, high at home),
* **inter-network hop**: traffic between different ASes crosses an exchange point,
  adding a small per-pair delay even inside one metro area,
* **jitter**: positive, heavy-tailed queueing delay per sample.

Per-pair parameters are derived from a hash of ``(seed, pair)`` so the model needs O(n)
memory and the same seed always yields the same world.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field

from .genesis import Genesis
from .geo import min_rtt_ms
from .graph import pair
from .model import Continent, Location, Node, NodeId

# (city, ISO country, continent, lat, lon) - public coordinates of major hubs.
CITIES: tuple[tuple[str, str, Continent, float, float], ...] = (
    ("Warsaw", "PL", Continent.EUROPE, 52.23, 21.01),
    ("Frankfurt", "DE", Continent.EUROPE, 50.11, 8.68),
    ("Amsterdam", "NL", Continent.EUROPE, 52.37, 4.90),
    ("London", "GB", Continent.EUROPE, 51.51, -0.13),
    ("Paris", "FR", Continent.EUROPE, 48.86, 2.35),
    ("Madrid", "ES", Continent.EUROPE, 40.42, -3.70),
    ("Milan", "IT", Continent.EUROPE, 45.46, 9.19),
    ("Stockholm", "SE", Continent.EUROPE, 59.33, 18.07),
    ("Vienna", "AT", Continent.EUROPE, 48.21, 16.37),
    ("Helsinki", "FI", Continent.EUROPE, 60.17, 24.94),
    ("Ashburn", "US", Continent.NORTH_AMERICA, 39.04, -77.49),
    ("New York", "US", Continent.NORTH_AMERICA, 40.71, -74.01),
    ("Chicago", "US", Continent.NORTH_AMERICA, 41.88, -87.63),
    ("Dallas", "US", Continent.NORTH_AMERICA, 32.78, -96.80),
    ("Los Angeles", "US", Continent.NORTH_AMERICA, 34.05, -118.24),
    ("Seattle", "US", Continent.NORTH_AMERICA, 47.61, -122.33),
    ("Toronto", "CA", Continent.NORTH_AMERICA, 43.65, -79.38),
    ("Mexico City", "MX", Continent.NORTH_AMERICA, 19.43, -99.13),
    ("Sao Paulo", "BR", Continent.SOUTH_AMERICA, -23.55, -46.63),
    ("Buenos Aires", "AR", Continent.SOUTH_AMERICA, -34.60, -58.38),
    ("Santiago", "CL", Continent.SOUTH_AMERICA, -33.45, -70.67),
    ("Bogota", "CO", Continent.SOUTH_AMERICA, 4.71, -74.07),
    ("Tokyo", "JP", Continent.ASIA, 35.68, 139.69),
    ("Seoul", "KR", Continent.ASIA, 37.57, 126.98),
    ("Singapore", "SG", Continent.ASIA, 1.35, 103.82),
    ("Hong Kong", "HK", Continent.ASIA, 22.32, 114.17),
    ("Mumbai", "IN", Continent.ASIA, 19.08, 72.88),
    ("Dubai", "AE", Continent.ASIA, 25.20, 55.27),
    ("Jakarta", "ID", Continent.ASIA, -6.21, 106.85),
    ("Sydney", "AU", Continent.OCEANIA, -33.87, 151.21),
    ("Melbourne", "AU", Continent.OCEANIA, -37.81, 144.96),
    ("Auckland", "NZ", Continent.OCEANIA, -36.85, 174.76),
    ("Johannesburg", "ZA", Continent.AFRICA, -26.20, 28.05),
    ("Lagos", "NG", Continent.AFRICA, 6.52, 3.38),
    ("Nairobi", "KE", Continent.AFRICA, -1.29, 36.82),
    ("Cairo", "EG", Continent.AFRICA, 30.04, 31.24),
)

#: Synthetic AS numbers from the private-use range (RFC 6996); no real operators implied.
PRIVATE_ASN_BASE = 64512


@dataclass(slots=True)
class WorldConfig:
    n_nodes: int = 300
    n_trusted: int = 100
    seed: int = 0
    datacenters_per_city: tuple[int, int] = (1, 3)
    #: share of nodes hosted at home / small offices instead of a datacenter
    edge_fraction: float = 0.2
    dc_access_ms: tuple[float, float] = (0.05, 0.4)
    edge_access_ms: tuple[float, float] = (2.0, 12.0)
    #: detour factor = 1 + lognormal(mu, sigma), clipped to ``max_detour``
    detour_mu: float = math.log(0.45)
    detour_sigma: float = 0.35
    max_detour: float = 3.0
    cross_network_ms: tuple[float, float] = (0.3, 1.2)
    tiv_fraction: float = 0.05
    tiv_inflation: tuple[float, float] = (1.5, 3.0)
    jitter_mean_ms: float = 0.3
    spike_probability: float = 0.02
    spike_ms: tuple[float, float] = (5.0, 60.0)
    cities: tuple[tuple[str, str, Continent, float, float], ...] = CITIES


@dataclass(slots=True)
class Datacenter:
    id: str
    location: Location


@dataclass(slots=True)
class World:
    """Ground truth: real locations, per-node access delays and the pairwise latency model."""

    config: WorldConfig
    nodes: dict[NodeId, Node] = field(default_factory=dict)
    true_location: dict[NodeId, Location] = field(default_factory=dict)
    access_ms: dict[NodeId, float] = field(default_factory=dict)
    datacenters: list[Datacenter] = field(default_factory=list)
    #: Extra one-way delay a node deliberately adds when answering (attack hook).
    added_delay_ms: dict[NodeId, float] = field(default_factory=dict)

    # -- deterministic per-pair randomness ---------------------------------------
    def _u(self, a: NodeId, b: NodeId, salt: str) -> float:
        x, y = pair(a, b)
        h = hashlib.blake2b(f"{self.config.seed}|{salt}|{x}|{y}".encode(), digest_size=8).digest()
        return (int.from_bytes(h, "big") + 0.5) / 2**64

    def detour(self, a: NodeId, b: NodeId) -> float:
        c = self.config
        u = self._u(a, b, "detour")
        # inverse-CDF of the normal via the error function (no scipy dependency)
        z = math.sqrt(2) * _erfinv(2 * u - 1)
        return min(c.max_detour, 1.0 + math.exp(c.detour_mu + c.detour_sigma * z))

    def tiv_inflation(self, a: NodeId, b: NodeId) -> float:
        c = self.config
        if self._u(a, b, "tiv") >= c.tiv_fraction:
            return 1.0
        lo, hi = c.tiv_inflation
        return lo + (hi - lo) * self._u(a, b, "tiv-amount")

    # -- latency -----------------------------------------------------------------
    def base_rtt(self, a: NodeId, b: NodeId) -> float:
        """Noise-free RTT between two nodes (what an infinite ping burst would converge to)."""
        if a == b:
            return 0.0
        a, b = pair(a, b)  # canonical order -> bit-exact symmetry
        la, lb = self.true_location[a], self.true_location[b]
        prop = min_rtt_ms(la.distance_km(lb)) * self.detour(a, b) * self.tiv_inflation(a, b)
        hop = 0.0
        if la.asn != lb.asn:
            lo, hi = self.config.cross_network_ms
            hop = lo + (hi - lo) * self._u(a, b, "ixp")
        return prop + hop + self.access_ms[a] + self.access_ms[b]

    def sample_rtt(self, a: NodeId, b: NodeId, rng: random.Random) -> float:
        c = self.config
        v = self.base_rtt(a, b) + rng.expovariate(1.0 / c.jitter_mean_ms)
        if rng.random() < c.spike_probability:
            v += rng.uniform(*c.spike_ms)
        # a node can only make itself look *farther*, by delaying its replies
        return v + 2 * self.added_delay_ms.get(a, 0.0) + 2 * self.added_delay_ms.get(b, 0.0)

    def ping(self, a: NodeId, b: NodeId, rng: random.Random, count: int = 5) -> float:
        """Minimum over a burst of ``count`` pings - the usual RTT estimator."""
        return min(self.sample_rtt(a, b, rng) for _ in range(count))

    # -- topology ----------------------------------------------------------------
    def add_node(
        self,
        node_id: NodeId,
        location: Location,
        access_ms: float,
        *,
        trusted: bool = False,
        claimed: Location | None = None,
        meta: dict[str, object] | None = None,
    ) -> Node:
        """Add a node; ``claimed`` lets adversarial nodes declare a false location."""
        if node_id in self.nodes:
            raise ValueError(f"duplicate node id {node_id!r}")
        node = Node(node_id, trusted, claimed or location, dict(meta or {}))
        self.nodes[node_id] = node
        self.true_location[node_id] = location
        self.access_ms[node_id] = access_ms
        return node

    def trusted_ids(self) -> list[NodeId]:
        return [n.id for n in self.nodes.values() if n.trusted]

    def genesis(self, rng: random.Random | None = None, pings: int = 10) -> Genesis:
        """Measure every pair of trusted nodes and build a validated genesis."""
        rng = rng or random.Random(self.config.seed ^ 0x6E6E)
        ids = self.trusted_ids()
        rtt = {
            (a, b): round(self.ping(a, b, rng, pings), 3)
            for i, a in enumerate(ids)
            for b in ids[i + 1 :]
        }
        return Genesis.build(
            [self.nodes[i] for i in ids], rtt, name=f"synthetic-{self.config.seed}"
        )


def generate_world(config: WorldConfig | None = None) -> World:
    """Create a world with datacenters in the configured cities and nodes spread over them.

    Trusted nodes are placed first, round-robin over cities, so the genesis covers every
    region before any city gets a second trusted node.
    """
    c = config or WorldConfig()
    if not 0 <= c.n_trusted <= c.n_nodes:
        raise ValueError("need 0 <= n_trusted <= n_nodes")
    rng = random.Random(c.seed)
    world = World(config=c)

    asn = PRIVATE_ASN_BASE
    city_dcs: list[list[Datacenter]] = []
    for name, country, cont, lat, lon in c.cities:
        dcs = []
        for k in range(rng.randint(*c.datacenters_per_city)):
            asn += 1
            dlat, dlon = _offset(lat, lon, rng.uniform(2, 30), rng.uniform(0, 360))
            loc = Location(dlat, dlon, country, name, cont, asn, f"{name.lower()}-dc{k + 1}")
            dc = Datacenter(loc.datacenter or "", loc)
            dcs.append(dc)
            world.datacenters.append(dc)
        city_dcs.append(dcs)

    order = list(range(len(c.cities)))
    for i in range(c.n_nodes):
        trusted = i < c.n_trusted
        city_idx = order[i % len(order)] if trusted else rng.randrange(len(order))
        if i % len(order) == len(order) - 1:
            rng.shuffle(order)
        dc = rng.choice(city_dcs[city_idx])
        edge = (not trusted) and rng.random() < c.edge_fraction
        if edge:
            lat, lon = _offset(
                dc.location.lat, dc.location.lon, rng.uniform(5, 60), rng.uniform(0, 360)
            )
            base = dc.location
            loc = Location(lat, lon, base.country, base.city, base.continent, asn + 1 + i, None)
            access = rng.uniform(*c.edge_access_ms)
        else:
            loc = dc.location
            access = rng.uniform(*c.dc_access_ms)
        world.add_node(f"n{i:05d}", loc, access, trusted=trusted)
    return world


def _offset(lat: float, lon: float, km: float, bearing_deg: float) -> tuple[float, float]:
    """Move ``km`` from (lat, lon) along a bearing (small-distance approximation)."""
    b = math.radians(bearing_deg)
    dlat = (km * math.cos(b)) / 111.32
    dlon = (km * math.sin(b)) / (111.32 * max(0.1, math.cos(math.radians(lat))))
    new_lon = (lon + dlon + 180.0) % 360.0 - 180.0
    return max(-90.0, min(90.0, lat + dlat)), new_lon


def _erfinv(y: float) -> float:
    """Inverse error function (Giles 2010 single-precision approximation, refined by Newton)."""
    y = max(-1 + 1e-15, min(1 - 1e-15, y))
    w = -math.log((1.0 - y) * (1.0 + y))
    if w < 5.0:
        w -= 2.5
        p = 2.81022636e-08
        for coef in (
            3.43273939e-07,
            -3.5233877e-06,
            -4.39150654e-06,
            0.00021858087,
            -0.00125372503,
            -0.00417768164,
            0.246640727,
            1.50140941,
        ):
            p = coef + p * w
    else:
        w = math.sqrt(w) - 3.0
        p = -0.000200214257
        for coef in (
            0.000100950558,
            0.00134934322,
            -0.00367342844,
            0.00573950773,
            -0.0076224613,
            0.00943887047,
            1.00167406,
            2.83297682,
        ):
            p = coef + p * w
    x = p * y
    for _ in range(2):  # Newton steps on erf(x) - y
        x -= (math.erf(x) - y) / (2 / math.sqrt(math.pi) * math.exp(-x * x))
    return x
