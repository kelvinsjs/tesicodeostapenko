"""Logic responsible for fetching tiles and preparing export artefacts."""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Dict, Iterable, Tuple

import mercantile
import numpy as np
import requests
from PIL import Image

from flask import Response, send_file

from ..config import HAVE_RASTERIO, from_bounds, rasterio, settings
from ..utils import calculate_area_km2, calculate_zoom_for_scale, create_world_file

TileKey = Tuple[int, int]


@dataclass
class ExportResult:
    file_obj: BytesIO
    mimetype: str
    download_name: str
    world_content: str | None
    info_header: str


def _download_tiles(
    tiles_list: Iterable[mercantile.Tile],
    zoom: int,
    session: requests.Session,
    temp_dir: str,
) -> Dict[TileKey, Image.Image]:
    """Download tiles and return a mapping of (x, y) -> PIL image."""
    images: Dict[TileKey, Image.Image] = {}
    headers = {"User-Agent": settings.user_agent}
    tiles_list = list(tiles_list)
    total = len(tiles_list)
    for idx, tile in enumerate(tiles_list, start=1):
        x, y = tile.x, tile.y
        url = f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
        print(f"[DEBUG] ({idx}/{total}) Downloading tile {x},{y} -> {url}")
        try:
            time.sleep(0.12)
            response = session.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                img = Image.open(BytesIO(response.content)).convert("RGB")
                images[(x, y)] = img
                img.save(os.path.join(temp_dir, f"tile_{x}_{y}.png"))
            elif response.status_code == 429:
                print("[WARN] Rate limited, sleeping 5s then retry")
                time.sleep(5)
                retry = session.get(url, headers=headers, timeout=30)
                if retry.status_code == 200:
                    img = Image.open(BytesIO(retry.content)).convert("RGB")
                    images[(x, y)] = img
                else:
                    print(f"[ERROR] Tile {x},{y} HTTP {retry.status_code}")
            else:
                print(f"[ERROR] Tile {x},{y} HTTP {response.status_code}")
        except Exception as exc:  # pragma: no cover - network issues
            print(f"[ERROR] Exception downloading tile {x},{y}: {exc}")
    return images


def _stitch_tiles(
    images: Dict[TileKey, Image.Image],
    tiles: Iterable[mercantile.Tile],
) -> Image.Image:
    tiles = list(tiles)
    xs = [tile.x for tile in tiles]
    ys = [tile.y for tile in tiles]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    cols = x_max - x_min + 1
    rows = y_max - y_min + 1
    total_width = cols * settings.tile_size
    total_height = rows * settings.tile_size

    canvas = Image.new("RGB", (total_width, total_height), (255, 255, 255))
    for (x, y), img in images.items():
        px = (x - x_min) * settings.tile_size
        py = (y - y_min) * settings.tile_size
        canvas.paste(img, (px, py))
    return canvas


def _crop_canvas(
    canvas: Image.Image,
    zoom: int,
    tiles: Iterable[mercantile.Tile],
    bounds: Tuple[float, float, float, float],
) -> Image.Image:
    tiles = list(tiles)
    xs = [tile.x for tile in tiles]
    ys = [tile.y for tile in tiles]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    total_width = (x_max - x_min + 1) * settings.tile_size
    total_height = (y_max - y_min + 1) * settings.tile_size

    b_tl = mercantile.bounds(x_min, y_min, zoom)
    b_br = mercantile.bounds(x_max, y_max, zoom)

    full_west = b_tl.west
    full_north = b_tl.north
    full_east = b_br.east
    full_south = b_br.south

    north, south, east, west = bounds

    def lon_to_px(lon: float) -> float:
        return (lon - full_west) / (full_east - full_west) * total_width

    def lat_to_py(lat: float) -> float:
        return (full_north - lat) / (full_north - full_south) * total_height

    left = int(round(lon_to_px(west)))
    right = int(round(lon_to_px(east)))
    top = int(round(lat_to_py(north)))
    bottom = int(round(lat_to_py(south)))

    left = max(0, min(total_width, left))
    right = max(0, min(total_width, right))
    top = max(0, min(total_height, top))
    bottom = max(0, min(total_height, bottom))

    if right <= left or bottom <= top:
        raise ValueError("Calculated empty crop box (check bbox and tile bounds)")

    return canvas.crop((left, top, right, bottom))


def prepare_map_download(
    north: float,
    south: float,
    east: float,
    west: float,
) -> ExportResult:
    print(f"\n[INFO] New request at {datetime.utcnow().isoformat()} Z")

    area_km2 = calculate_area_km2(north, south, east, west)
    if area_km2 > settings.max_area_km2:
        raise ValueError(
            f"Площадь превышает {settings.max_area_km2} км². "
            f"Текущая площадь: {area_km2:.1f} км²"
        )

    avg_lat = (north + south) / 2
    zoom = calculate_zoom_for_scale(settings.scale, avg_lat)
    print(f"[INFO] Using zoom {zoom}")

    tiles = list(mercantile.tiles(west, south, east, north, zoom))
    if not tiles:
        raise ValueError("No tiles to download (possible bbox error)")

    temp_dir = tempfile.mkdtemp(prefix="maptiles_")
    session = requests.Session()
    try:
        images = _download_tiles(tiles, zoom, session, temp_dir)
        canvas = _stitch_tiles(images, tiles)
        cropped = _crop_canvas(canvas, zoom, tiles, (north, south, east, west))

        filename_base = f"map_{north:.6f}_{south:.6f}_{east:.6f}_{west:.6f}"
        world_content: str | None = None

        if HAVE_RASTERIO and rasterio and from_bounds:
            out_path = os.path.join(temp_dir, f"{filename_base}.tif")
            arr = np.transpose(np.array(cropped), (2, 0, 1))
            transform = from_bounds(west, south, east, north, cropped.width, cropped.height)
            profile = {
                "driver": "GTiff",
                "height": cropped.height,
                "width": cropped.width,
                "count": 3,
                "dtype": arr.dtype,
                "crs": "EPSG:4326",
                "transform": transform,
            }
            with rasterio.open(out_path, "w", **profile) as dst:
                dst.write(arr)
            mimetype = "image/tiff"
        else:
            out_path = os.path.join(temp_dir, f"{filename_base}.png")
            cropped.save(out_path, dpi=(settings.dpi, settings.dpi))
            pgw_path = os.path.join(temp_dir, f"{filename_base}.pgw")
            world_content = create_world_file(
                north, south, east, west, cropped.width, cropped.height, pgw_path
            )
            mimetype = "image/png"

        with open(out_path, "rb") as fh:
            file_bytes = BytesIO(fh.read())

        download_name = os.path.basename(out_path)
        info_header = (
            f"DPI:{settings.dpi};Scale:1:{settings.scale};Area:{area_km2:.2f}km2;"
            f"Zoom:{zoom};Tiles:{len(tiles)}"
        )

        file_bytes.seek(0)
        return ExportResult(
            file_obj=file_bytes,
            mimetype=mimetype,
            download_name=download_name,
            world_content=world_content,
            info_header=info_header,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def build_export_response(export: ExportResult) -> Response:
    """Create a Flask response for the prepared export file."""
    response = send_file(
        export.file_obj,
        mimetype=export.mimetype,
        as_attachment=True,
        download_name=export.download_name,
    )
    if export.world_content:
        response.headers["X-World-File"] = export.world_content.replace("\n", ";")
    response.headers["X-Info"] = export.info_header
    return response


def summarize_area(north: float, south: float, east: float, west: float):
    area_km2 = calculate_area_km2(north, south, east, west)
    avg_lat = (north + south) / 2
    zoom = calculate_zoom_for_scale(settings.scale, avg_lat)
    tiles = list(mercantile.tiles(west, south, east, north, zoom))
    estimated_time = len(tiles) * 0.2
    return {
        "area_km2": area_km2,
        "valid": area_km2 <= settings.max_area_km2,
        "max_area": settings.max_area_km2,
        "total_tiles": len(tiles),
        "estimated_time": estimated_time,
        "zoom": zoom,
    }


__all__ = [
    "ExportResult",
    "prepare_map_download",
    "summarize_area",
    "build_export_response",
]
