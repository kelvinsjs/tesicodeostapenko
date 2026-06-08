"""Service helpers for computing Topographic Wetness Index (TWI) rasters."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from io import BytesIO
from typing import Tuple

import numpy as np
from flask import Response, send_file

from getting_image.getting_srtm import SRTMDownloadError, download_srtm_dem

from ..config import HAVE_RASTERIO, rasterio, settings
from ..utils import calculate_area_km2
from .map_export import ExportResult


class TWIGenerationError(RuntimeError):
    """Raised when TWI generation cannot be performed."""


def _debug(message: str) -> None:
    """Emit verbose debug logs for TWI pipeline."""
    import sys
    print(f"[TWI DEBUG] {message}", flush=True, file=sys.stderr)


def _estimate_utm_epsg(north: float, south: float, east: float, west: float) -> int:
    """Pick a UTM EPSG code based on the center of the requested area."""
    center_lat = (north + south) / 2
    center_lon = (east + west) / 2
    zone = int((center_lon + 180) / 6) + 1
    if center_lat >= 0:
        return 32600 + zone
    return 32700 + zone


def _reproject_dem_to_metric_grid(dem_path: str, *, target_epsg: int) -> Tuple[np.ndarray, dict]:
    """Reproject DEM to a metric CRS for robust slope/area calculations."""
    assert rasterio is not None
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    _debug(f"Opening DEM: {dem_path}")
    with rasterio.open(dem_path) as src:
        _debug(
            f"Source DEM: crs={src.crs}, size={src.width}x{src.height}, "
            f"dtype={src.dtypes[0]}, nodata={src.nodata}"
        )
        vrt_options = {
            "crs": f"EPSG:{target_epsg}",
            "resampling": Resampling.bilinear,
        }
        with WarpedVRT(src, **vrt_options) as vrt:
            _debug(
                f"WarpedVRT: target_crs=EPSG:{target_epsg}, "
                f"size={vrt.width}x{vrt.height}, dtype={vrt.dtypes[0]}"
            )
            # ВАЖНО: сначала .astype("float32"), потом .filled(np.nan)
            # иначе MaskedArray с dtype=int16 не может принять fill_value=nan
            raw_masked = vrt.read(1, masked=True)
            _debug(f"Raw masked array: dtype={raw_masked.dtype}, shape={raw_masked.shape}")
            dem = raw_masked.astype("float32").filled(np.nan)
            _debug(
                f"DEM converted: shape={dem.shape}, "
                f"finite={int(np.isfinite(dem).sum())}, nan={int(np.isnan(dem).sum())}"
            )
            profile = vrt.profile.copy()
            profile.update(
                {
                    "driver": "GTiff",
                    "dtype": "float32",
                    "count": 1,
                    "nodata": -9999.0,
                }
            )
            _debug(f"Profile updated: {profile}")
    return dem, profile


def _fill_nodata(dem: np.ndarray) -> np.ndarray:
    """Fill nodata holes in DEM using nearest valid values."""
    assert rasterio is not None
    from rasterio.fill import fillnodata

    invalid = np.isnan(dem)
    invalid_count = int(invalid.sum())
    _debug(f"Fill nodata: invalid_pixels={invalid_count}, total={dem.size}")

    if not invalid.any():
        _debug("Fill nodata skipped: no invalid pixels")
        return dem

    dem_f32 = dem.astype("float32")
    mask_u8 = (~invalid).astype("uint8")
    _debug(f"Calling fillnodata: valid_pixels={int(mask_u8.sum())}, search_distance=100")

    filled = fillnodata(dem_f32, mask=mask_u8, max_search_distance=100)
    restored = np.where(np.isnan(filled), dem, filled).astype("float32")
    _debug(f"Fill nodata done: remaining_nan={int(np.isnan(restored).sum())}")
    return restored


def _slope_radians_zevenbergen_thorne(dem: np.ndarray, xres: float, yres: float) -> np.ndarray:
    """Calculate slope in radians via central differences (Zevenbergen-Thorne style)."""
    _debug(f"Slope calculation: xres={xres:.4f}m, yres={yres:.4f}m, shape={dem.shape}")

    dzdx = np.zeros_like(dem, dtype="float32")
    dzdy = np.zeros_like(dem, dtype="float32")

    dzdx[:, 1:-1] = (dem[:, 2:] - dem[:, :-2]) / (2 * xres)
    dzdy[1:-1, :] = (dem[:-2, :] - dem[2:, :]) / (2 * yres)

    slope = np.arctan(np.sqrt(dzdx * dzdx + dzdy * dzdy))
    result = slope.astype("float32")
    _debug(
        f"Slope done: min={float(np.nanmin(result)):.6f} rad, "
        f"max={float(np.nanmax(result)):.6f} rad"
    )
    return result


def _flow_accumulation_pixels(dem: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
    """Compute D8 contributing area in pixels."""
    h, w = dem.shape
    _debug(f"Flow accumulation start: grid={h}x{w}, valid={int(valid_mask.sum())}")
    size = h * w
    offsets = [
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1),
    ]

    receiver = np.full(size, -1, dtype=np.int32)
    indegree = np.zeros(size, dtype=np.int32)
    flat = dem.ravel()
    valid_flat = valid_mask.ravel()

    for r in range(h):
        for c in range(w):
            idx = r * w + c
            if not valid_flat[idx]:
                continue

            z = flat[idx]
            best_drop = 0.0
            best_idx = -1
            for dr, dc in offsets:
                rr, cc = r + dr, c + dc
                if rr < 0 or rr >= h or cc < 0 or cc >= w:
                    continue
                n_idx = rr * w + cc
                if not valid_flat[n_idx]:
                    continue
                drop = z - flat[n_idx]
                if drop > best_drop:
                    best_drop = drop
                    best_idx = n_idx

            receiver[idx] = best_idx
            if best_idx >= 0:
                indegree[best_idx] += 1

    _debug("D8 receiver assignment done, starting accumulation...")

    acc = np.zeros(size, dtype="float64")
    acc[valid_flat] = 1.0

    queue = [int(i) for i in np.where(valid_flat & (indegree == 0))[0]]
    _debug(f"Queue initialized: {len(queue)} source pixels")
    head = 0
    while head < len(queue):
        idx = queue[head]
        head += 1
        dst = receiver[idx]
        if dst >= 0:
            acc[dst] += acc[idx]
            indegree[dst] -= 1
            if indegree[dst] == 0:
                queue.append(int(dst))

    result = acc.reshape((h, w)).astype("float32")
    _debug(
        f"Flow accumulation done: min={float(np.nanmin(result)):.1f}, "
        f"max={float(np.nanmax(result)):.1f}"
    )
    return result


def prepare_twi_download(
    *, north: float, south: float, east: float, west: float
) -> ExportResult:
    """Generate a TWI raster for the selected extent."""
    _debug(f"=== TWI pipeline start ===")
    _debug(f"Bounds: north={north}, south={south}, east={east}, west={west}")

    if not HAVE_RASTERIO or rasterio is None:
        raise TWIGenerationError("Библиотека rasterio недоступна на сервере.")

    area_km2 = calculate_area_km2(north, south, east, west)
    _debug(f"Area: {area_km2:.3f} km2, limit: {settings.max_area_km2} km2")
    if area_km2 > settings.max_area_km2:
        raise ValueError(
            f"Площадь превышает {settings.max_area_km2} км². "
            f"Текущая площадь: {area_km2:.1f} км²"
        )

    _debug("Downloading SRTM DEM...")
    srtm = download_srtm_dem(
        north=north,
        south=south,
        east=east,
        west=west,
        api_key=settings.dem_api_key,
    )
    _debug(
        f"DEM downloaded: bytes={len(srtm.content)}, "
        f"filename={srtm.filename}, mimetype={srtm.mimetype}"
    )

    with tempfile.TemporaryDirectory(prefix="twi_") as tmpdir:
        dem_path = os.path.join(tmpdir, "dem.tif")
        with open(dem_path, "wb") as fh:
            fh.write(srtm.content)
        _debug(f"DEM saved to: {dem_path}")

        target_epsg = _estimate_utm_epsg(north, south, east, west)
        _debug(f"Target CRS: EPSG:{target_epsg}")

        _debug("Step 1/5: Reprojecting DEM to metric grid...")
        dem, profile = _reproject_dem_to_metric_grid(dem_path, target_epsg=target_epsg)

        _debug("Step 2/5: Filling nodata...")
        dem_filled = _fill_nodata(dem)

        transform = profile["transform"]
        xres = abs(transform.a)
        yres = abs(transform.e)
        pixel_area_m2 = xres * yres
        _debug(f"Pixel size: xres={xres:.4f}m, yres={yres:.4f}m, area={pixel_area_m2:.4f}m2")
        if pixel_area_m2 <= 0:
            raise TWIGenerationError("Не удалось определить размер пикселя в метрах.")

        valid = np.isfinite(dem_filled)
        _debug(f"Valid pixels: {int(valid.sum())} / {valid.size}")

        _debug("Step 3/5: Computing slope...")
        slope_rad = _slope_radians_zevenbergen_thorne(dem_filled, xres, yres)

        _debug("Step 4/5: Computing flow accumulation (D8)...")
        flow_pixels = _flow_accumulation_pixels(dem_filled, valid)

        _debug("Step 5/5: Computing TWI...")
        contrib_area_m2 = flow_pixels * pixel_area_m2
        _debug(
            f"Contributing area m2: min={float(np.nanmin(contrib_area_m2)):.1f}, "
            f"max={float(np.nanmax(contrib_area_m2)):.1f}"
        )
        slope_safe = np.where(slope_rad > 1e-6, slope_rad, np.nan)

        twi = np.full(dem_filled.shape, -9999.0, dtype="float32")
        with np.errstate(divide="ignore", invalid="ignore"):
            twi_calc = np.log(contrib_area_m2 / np.tan(slope_safe))

        good = valid & np.isfinite(twi_calc)
        _debug(f"TWI pixels: good={int(good.sum())}, bad={int((~good & valid).sum())}")
        twi[good] = twi_calc[good].astype("float32")

        out_path = os.path.join(tmpdir, "twi.tif")
        _debug(f"Writing GeoTIFF: {out_path}")
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(twi, 1)

        file_obj = BytesIO()
        with open(out_path, "rb") as fh:
            file_obj.write(fh.read())
        file_obj.seek(0)
        _debug(f"Output ready: {file_obj.getbuffer().nbytes} bytes")

    info_header = (
        f"TWI;CRS:EPSG:{target_epsg};PixelAreaM2:{pixel_area_m2:.3f};"
        f"Area:{area_km2:.2f}km2;Requested:{datetime.utcnow().isoformat()}Z"
    )

    _debug("=== TWI pipeline finished successfully ===")

    return ExportResult(
        file_obj=file_obj,
        mimetype="image/tiff",
        download_name="twi.tif",
        world_content=None,
        info_header=info_header,
    )


def build_twi_response(export: ExportResult) -> Response:
    """Create a Flask response for TWI downloads."""
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
    "prepare_twi_download",
    "build_twi_response",
    "TWIGenerationError",
    "SRTMDownloadError",
]