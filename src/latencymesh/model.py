"""Core value types: locations, nodes and RTT measurements."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .geo import haversine_km

NodeId = str


class Continent(StrEnum):
    AFRICA = "AF"
    ANTARCTICA = "AN"
    ASIA = "AS"
    EUROPE = "EU"
    NORTH_AMERICA = "NA"
    OCEANIA = "OC"
    SOUTH_AMERICA = "SA"


@dataclass(frozen=True, slots=True)
class Location:
    """Where a node is (for trusted nodes) or claims to be (for everyone else)."""

    lat: float
    lon: float
    country: str | None = None  # ISO 3166-1 alpha-2
    city: str | None = None
    continent: Continent | None = None
    asn: int | None = None
    datacenter: str | None = None

    def __post_init__(self) -> None:
        if not -90.0 <= self.lat <= 90.0:
            raise ValueError(f"latitude out of range: {self.lat}")
        if not -180.0 <= self.lon <= 180.0:
            raise ValueError(f"longitude out of range: {self.lon}")

    def distance_km(self, other: Location) -> float:
        return haversine_km(self.lat, self.lon, other.lat, other.lon)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lat": self.lat,
            "lon": self.lon,
            "country": self.country,
            "city": self.city,
            "continent": self.continent.value if self.continent else None,
            "asn": self.asn,
            "datacenter": self.datacenter,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Location:
        cont = d.get("continent")
        return cls(
            lat=float(d["lat"]),
            lon=float(d["lon"]),
            country=d.get("country"),
            city=d.get("city"),
            continent=Continent(cont) if cont else None,
            asn=d.get("asn"),
            datacenter=d.get("datacenter"),
        )


@dataclass(frozen=True, slots=True)
class Node:
    """A participant of the network.

    ``trusted`` nodes come from the genesis and their ``location`` is authoritative.
    For any other node ``location`` is only a *claim* that has to be verified.
    """

    id: NodeId
    trusted: bool = False
    location: Location | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "trusted": self.trusted,
            "location": self.location.to_dict() if self.location else None,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Node:
        loc = d.get("location")
        return cls(
            id=str(d["id"]),
            trusted=bool(d.get("trusted", False)),
            location=Location.from_dict(loc) if loc else None,
            meta=dict(d.get("meta") or {}),
        )


@dataclass(frozen=True, slots=True)
class Measurement:
    """One RTT observation made by ``observer`` towards ``target``.

    ``rtt_ms`` should already be the minimum over a short burst of pings (``samples``):
    the minimum filters out queueing and best reflects path length.
    """

    observer: NodeId
    target: NodeId
    rtt_ms: float
    time: float = 0.0
    samples: int = 1

    def __post_init__(self) -> None:
        if self.observer == self.target:
            raise ValueError("a node cannot measure itself")
        if not self.rtt_ms >= 0.0:  # also rejects NaN
            raise ValueError(f"invalid rtt: {self.rtt_ms}")
        if self.samples < 1:
            raise ValueError("samples must be >= 1")
