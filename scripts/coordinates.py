"""Coordinate validation shared by location and map providers."""
import math


def valid_coordinates(lat, lon):
    try:
        if isinstance(lat, bool) or isinstance(lon, bool):
            return False
        lat, lon = float(lat), float(lon)
        return math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180
    except (TypeError, ValueError):
        return False
