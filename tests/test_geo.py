import pytest

from latencymesh.geo import haversine_km, max_distance_km, min_rtt_ms


def test_haversine_warsaw_berlin() -> None:
    d = haversine_km(52.2297, 21.0122, 52.5200, 13.4050)
    assert d == pytest.approx(517, abs=5)


def test_speed_of_light_roundtrip() -> None:
    assert min_rtt_ms(1000.0) == pytest.approx(10.0, rel=0.01)
    assert max_distance_km(min_rtt_ms(1234.0)) == pytest.approx(1234.0)
