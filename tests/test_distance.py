from __future__ import annotations

from emploi.france_travail.distance import (
    KNOWN_LOCATIONS,
    distance_km,
    resolve_location_point,
    within_requested_radius,
)


class TestResolveLocationPoint:
    def test_known_location_bogeve(self):
        point = resolve_location_point("Bogève")
        assert point is not None
        assert point.name == "Bogève"
        assert abs(point.lat - 46.193333) < 0.001

    def test_known_location_annemasse(self):
        point = resolve_location_point("Annemasse")
        assert point is not None
        assert point.name == "Annemasse"

    def test_postcode_hint(self):
        point = resolve_location_point("74250")
        assert point is not None
        assert point.name == "Bogève"

    def test_unknown_location_returns_none(self):
        point = resolve_location_point("Tokyo")
        assert point is None

    def test_empty_string_returns_none(self):
        assert resolve_location_point("") is None

    def test_whitespace_only_returns_none(self):
        assert resolve_location_point("   ") is None


class TestDistanceKm:
    def test_same_point_is_zero(self):
        bogeve = KNOWN_LOCATIONS["bogeve"]
        assert distance_km(bogeve, bogeve) == 0.0

    def test_bogeve_to_annemasse(self):
        bogeve = KNOWN_LOCATIONS["bogeve"]
        annemasse = KNOWN_LOCATIONS["annemasse"]
        d = distance_km(bogeve, annemasse)
        # Bogève to Annemasse is roughly 15-20 km
        assert 10 < d < 25

    def test_bogeve_to_bonneville(self):
        bogeve = KNOWN_LOCATIONS["bogeve"]
        bonneville = KNOWN_LOCATIONS["bonneville"]
        d = distance_km(bogeve, bonneville)
        assert 5 < d < 20


class TestWithinRequestedRadius:
    def test_zero_radius_always_true(self):
        assert within_requested_radius("Bogève", "Annemasse", 0) is True

    def test_empty_origin_always_true(self):
        assert within_requested_radius("", "Annemasse", 20) is True

    def test_empty_destination_always_true(self):
        assert within_requested_radius("Bogève", "", 20) is True

    def test_known_within_radius(self):
        assert within_requested_radius("Bogève", "Annemasse", 20) is True

    def test_known_outside_radius(self):
        assert within_requested_radius("Bogève", "Annemasse", 5) is False

    def test_unknown_destination_returns_true(self):
        # Unknown locations should not be filtered out
        assert within_requested_radius("Bogève", "Tokyo", 10) is True
