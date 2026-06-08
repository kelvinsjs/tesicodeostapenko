"""Utility helpers for downloading SRTM elevation rasters."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests


BASE_URL = "https://portal.opentopography.org/API/globaldem"
DEM_TYPE = "SRTMGL1"
OUTPUT_FORMAT = "GTiff"


class SRTMDownloadError(RuntimeError):
    """Raised when the OpenTopography API returns an error."""


@dataclass(slots=True)
class SRTMDownloadResult:
    """Result of an SRTM download request."""

    filename: str
    mimetype: str
    content: bytes


def _infer_filename(content_disposition: Optional[str], fallback: str) -> str:
    """Extract the filename from the HTTP content-disposition header."""

    if not content_disposition:
        return fallback

    parts = content_disposition.split(";")
    for part in parts:
        if "filename=" in part:
            candidate = part.split("=", 1)[1].strip().strip('"')
            if candidate:
                return candidate
    return fallback


def download_srtm_dem(
    *,
    north: float,
    south: float,
    east: float,
    west: float,
    api_key: str,
    session: requests.Session | None = None,
    timeout: int = 120,
) -> SRTMDownloadResult:
    """Download SRTM elevation data for the provided bounding box."""

    if north <= south:
        raise ValueError("north must be greater than south")
    if east <= west:
        raise ValueError("east must be greater than west")
    if not api_key:
        raise ValueError("OpenTopography API key is required")

    params = {
        "demtype": DEM_TYPE,
        "south": south,
        "north": north,
        "west": west,
        "east": east,
        "outputFormat": OUTPUT_FORMAT,
        "API_Key": api_key,
    }

    http = session or requests.Session()
    response = http.get(BASE_URL, params=params, stream=True, timeout=timeout)

    if response.status_code != 200:
        detail = response.text[:500]
        raise SRTMDownloadError(
            f"OpenTopography API error {response.status_code}: {detail}"
        )

    chunks: list[bytes] = []
    for chunk in response.iter_content(chunk_size=8192):
        if chunk:
            chunks.append(chunk)

    content = b"".join(chunks)

    mimetype = response.headers.get("Content-Type", "application/octet-stream")
    fallback_name = (
        f"SRTM_{north:.4f}_{south:.4f}_{east:.4f}_{west:.4f}.tif"
    )
    filename = _infer_filename(response.headers.get("Content-Disposition"), fallback_name)

    return SRTMDownloadResult(filename=filename, mimetype=mimetype, content=content)


__all__ = [
    "SRTMDownloadResult",
    "SRTMDownloadError",
    "download_srtm_dem",
]

