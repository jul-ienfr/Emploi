from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LocationPoint:
    name: str
    lat: float
    lon: float


# Minimal offline gazetteer for Julien's active France Travail operating area.
# Distances are used as a strict client-side cap after France Travail returns
# the nearest upper supported radius (e.g. requested 15 km -> FT rayon 20 km).
KNOWN_LOCATIONS: dict[str, LocationPoint] = {
    "bogeve": LocationPoint("Bogève", 46.193333, 6.430278),
    "bogève": LocationPoint("Bogève", 46.193333, 6.430278),
    "bonne": LocationPoint("Bonne", 46.167646, 6.322647),
    "bonneville": LocationPoint("Bonneville", 46.077580, 6.408619),
    "bons en chablais": LocationPoint("Bons-en-Chablais", 46.264918, 6.370282),
    "bons-en-chablais": LocationPoint("Bons-en-Chablais", 46.264918, 6.370282),
    "annemasse": LocationPoint("Annemasse", 46.193401, 6.234109),
    "saint pierre en faucigny": LocationPoint("Saint-Pierre-en-Faucigny", 46.059990, 6.372410),
    "saint-pierre-en-faucigny": LocationPoint("Saint-Pierre-en-Faucigny", 46.059990, 6.372410),
    "perrignier": LocationPoint("Perrignier", 46.306464, 6.440684),
}

POSTCODE_HINTS: dict[str, str] = {
    "74250": "bogève",
    "74100": "annemasse",
    "74130": "bonneville",
    "74380": "bonne",
    "74890": "bons-en-chablais",
    "74800": "saint-pierre-en-faucigny",
    "74550": "perrignier",
}


def _key(value: str) -> str:
    text = value.casefold()
    text = re.sub(r"\b\d{2}\s*-\s*", " ", text)
    text = re.sub(r"\bfrance\b", " ", text)
    text = text.replace("’", "'").replace("-", " ")
    text = re.sub(r"[^\w\s']+", " ", text, flags=re.U)
    return re.sub(r"\s+", " ", text).strip()


def resolve_location_point(value: str) -> LocationPoint | None:
    text = value.strip()
    if not text:
        return None
    lowered = _key(text)
    if lowered in KNOWN_LOCATIONS:
        return KNOWN_LOCATIONS[lowered]
    for postcode, canonical in POSTCODE_HINTS.items():
        if postcode in text:
            return KNOWN_LOCATIONS.get(canonical)
    for alias, point in KNOWN_LOCATIONS.items():
        if re.search(rf"\b{re.escape(alias.replace('-', ' '))}\b", lowered):
            return point
    return None


def distance_km(origin: LocationPoint, destination: LocationPoint) -> float:
    radius_km = 6371.0088
    lat1 = math.radians(origin.lat)
    lat2 = math.radians(destination.lat)
    d_lat = lat2 - lat1
    d_lon = math.radians(destination.lon - origin.lon)
    a = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(a))


def within_requested_radius(origin_text: str, destination_text: str, requested_radius_km: int) -> bool:
    if requested_radius_km <= 0 or not origin_text or not destination_text:
        return True
    origin = resolve_location_point(origin_text)
    destination = resolve_location_point(destination_text)
    if origin is None or destination is None:
        # Unknown locations should not be silently dropped; France Travail's
        # own radius remains the fallback signal.
        return True
    return distance_km(origin, destination) <= float(requested_radius_km)
