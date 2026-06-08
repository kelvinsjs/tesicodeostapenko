"""Utility helpers used across the application."""
from __future__ import annotations

import math

from .config import settings


def calculate_zoom_for_scale(scale: int, lat: float) -> int:
    """Calculate an approximate zoom level for a given map scale and latitude."""
    meters_per_pixel = scale * 0.0254 / settings.dpi
    lat_rad = math.radians(lat)
    zoom = math.log2(156543.03392 * math.cos(lat_rad) / meters_per_pixel)
    return min(18, max(0, int(round(zoom))))


def calculate_area_km2(north: float, south: float, east: float, west: float) -> float:
    """Rough area calculation for the selected bounding box in square kilometers."""
    lat_diff = abs(north - south)
    lon_diff = abs(east - west)
    lat_km = lat_diff * 111
    avg_lat = (north + south) / 2
    lon_km = lon_diff * 111 * math.cos(math.radians(avg_lat))
    return lat_km * lon_km


def create_world_file(
    north: float,
    south: float,
    east: float,
    west: float,
    width: int,
    height: int,
    filename: str,
) -> str:
    """Create a PNG world file describing geographic placement."""
    x_resolution = (east - west) / width
    y_resolution = -(north - south) / height
    world_content = (
        f"{x_resolution}\n0.0\n0.0\n{y_resolution}\n"
        f"{west + x_resolution / 2}\n{north + y_resolution / 2}"
    )
    with open(filename, "w", encoding="utf-8") as fh:
        fh.write(world_content)
    return world_content


__all__ = [
    "calculate_area_km2",
    "calculate_zoom_for_scale",
    "create_world_file",
]
