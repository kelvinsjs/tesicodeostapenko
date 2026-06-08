"""Service helpers for working with DEM exports."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO

from flask import Response, send_file

from getting_image.getting_srtm import SRTMDownloadError, download_srtm_dem

from ..config import settings
from ..utils import calculate_area_km2
from .map_export import ExportResult


def prepare_dem_download(
    *, north: float, south: float, east: float, west: float
) -> ExportResult:
    """Fetch DEM data for the selected extent and return as an export result."""

    area_km2 = calculate_area_km2(north, south, east, west)
    if area_km2 > settings.max_area_km2:
        raise ValueError(
            f"Площадь превышает {settings.max_area_km2} км². "
            f"Текущая площадь: {area_km2:.1f} км²"
        )

    result = download_srtm_dem(
        north=north,
        south=south,
        east=east,
        west=west,
        api_key=settings.dem_api_key,
    )

    file_obj = BytesIO(result.content)
    file_obj.seek(0)
    info_header = (
        f"DEM:SRTMGL1;CRS:EPSG:4326;Area:{area_km2:.2f}km2;"
        f"Requested:{datetime.utcnow().isoformat()}Z"
    )

    return ExportResult(
        file_obj=file_obj,
        mimetype=result.mimetype,
        download_name=result.filename,
        world_content=None,
        info_header=info_header,
    )


def build_dem_response(export: ExportResult) -> Response:
    """Create a Flask response for DEM downloads."""

    response = send_file(
        export.file_obj,
        mimetype=export.mimetype,
        as_attachment=True,
        download_name=export.download_name,
    )
    response.headers["X-Filename"] = export.download_name
    response.headers["X-Info"] = export.info_header
    return response


__all__ = ["prepare_dem_download", "build_dem_response", "SRTMDownloadError"]

