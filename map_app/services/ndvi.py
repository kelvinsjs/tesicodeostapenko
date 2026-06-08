"""Service responsible for generating NDVI rasters from prepared Sentinel mosaics."""
from __future__ import annotations

import math
import os
import tempfile
from datetime import datetime
from io import BytesIO
from typing import TYPE_CHECKING, Tuple

import numpy as np
from flask import Response, send_file

from ..config import HAVE_RASTERIO, from_bounds, rasterio, settings
from ..utils import calculate_area_km2
from .map_export import ExportResult


class NDVIGenerationError(RuntimeError):
    """Raised when NDVI generation cannot be performed."""


if TYPE_CHECKING:  # pragma: no cover - typing only
    from rasterio.transform import Affine


def _resolve_band_path(band_name: str) -> str:
    path = os.path.join(settings.sentinel_union_dir, band_name)
    if not os.path.exists(path):
        raise NDVIGenerationError(
            "Подготовленные снимки Sentinel-2 не найдены. "
            "Проверьте путь в переменной окружения SENTINEL_UNION_DIR."
        )
    return path


def _target_grid(
    *,
    north: float,
    south: float,
    east: float,
    west: float,
) -> Tuple["Affine", int, int]:
    """Compute the output grid transform for the requested bounding box."""

    lat_extent = max(1e-6, north - south)
    lon_extent = max(1e-6, east - west)
    avg_lat = (north + south) / 2

    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = max(1e-6, 111_320.0 * math.cos(math.radians(avg_lat)))

    lat_resolution = 20.0 / meters_per_deg_lat
    lon_resolution = 20.0 / meters_per_deg_lon

    height = max(1, int(math.ceil(lat_extent / lat_resolution)))
    width = max(1, int(math.ceil(lon_extent / lon_resolution)))

    transform = from_bounds(west, south, east, north, width, height)
    return transform, width, height


def _read_band(
    dataset_path: str,
    *,
    transform: rasterio.Affine,
    width: int,
    height: int,
) -> np.ma.MaskedArray:
    """Read a single band into the target grid using a Warped VRT."""

    assert rasterio is not None  # for type checkers
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    with rasterio.open(dataset_path) as src:
        vrt_options = dict(
            crs="EPSG:4326",
            transform=transform,
            width=width,
            height=height,
            resampling=Resampling.bilinear,
        )
        with WarpedVRT(src, **vrt_options) as vrt:
            data = vrt.read(1, masked=True)
    return data


def prepare_ndvi_download(
    *,
    north: float,
    south: float,
    east: float,
    west: float,
) -> ExportResult:
    """Generate an NDVI raster for the provided bounding box."""

    if not HAVE_RASTERIO or rasterio is None or from_bounds is None:
        raise NDVIGenerationError("Библиотека rasterio недоступна на сервере.")

    area_km2 = calculate_area_km2(north, south, east, west)
    if area_km2 > settings.max_area_km2:
        raise ValueError(
            f"Площадь превышает {settings.max_area_km2} км². "
            f"Текущая площадь: {area_km2:.1f} км²"
        )

    red_path = _resolve_band_path("B04_20m_union.tif")
    nir_path = _resolve_band_path("B8A_20m_union.tif")

    transform, width, height = _target_grid(
        north=north, south=south, east=east, west=west
    )

    red = _read_band(red_path, transform=transform, width=width, height=height)
    nir = _read_band(nir_path, transform=transform, width=width, height=height)

    red_data = red.astype("float32")
    nir_data = nir.astype("float32")

    denominator = nir_data + red_data
    valid_mask = (~red.mask) & (~nir.mask) & (denominator != 0)

    ndvi = np.full((height, width), -9999.0, dtype="float32")
    ndvi[valid_mask] = (nir_data[valid_mask] - red_data[valid_mask]) / denominator[
        valid_mask
    ]

    info_header = (
        f"NDVI;Area:{area_km2:.2f}km2;"
        f"Requested:{datetime.utcnow().isoformat()}Z"
    )

    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": transform,
        "nodata": -9999.0,
    }

    file_obj = BytesIO()

    with tempfile.TemporaryDirectory(prefix="ndvi_") as temp_dir:
        out_path = os.path.join(temp_dir, "ndvi.tif")

        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(ndvi, 1)

        with open(out_path, "rb") as fh:
            file_obj.write(fh.read())

    file_obj.seek(0)

    return ExportResult(
        file_obj=file_obj,
        mimetype="image/tiff",
        download_name="ndvi.tif",
        world_content=None,
        info_header=info_header,
    )


def build_ndvi_response(export: ExportResult) -> Response:
    """Create a Flask response carrying the NDVI GeoTIFF."""

    response = send_file(
        export.file_obj,
        mimetype=export.mimetype,
        as_attachment=True,
        download_name=export.download_name,
    )
    response.headers["X-Filename"] = export.download_name
    response.headers["X-Info"] = export.info_header
    return response


__all__ = [
    "prepare_ndvi_download",
    "build_ndvi_response",
    "NDVIGenerationError",
]

