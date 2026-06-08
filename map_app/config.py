"""Application configuration constants and feature flags."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    max_area_km2: float = 10000
    dpi: int = 200
    scale: int = 100_000
    tile_size: int = 256
    user_agent: str = "MapDownloader/1.0 Educational Purpose"
    dem_api_key: str = os.getenv(
        "OPENTOPOGRAPHY_API_KEY",
        "6323d4f076d989ed5f5074f4df0dd8cb",
    )
    sentinel_union_dir: str = os.getenv(
        "SENTINEL_UNION_DIR",
        os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "images",
                "downloads",
                "union",
            )
        ),
    )


settings = Settings()

try:  # Optional rasterio support
    import rasterio  # type: ignore
    from rasterio.transform import from_bounds  # type: ignore
    HAVE_RASTERIO = True
except Exception:  # pragma: no cover - optional dependency
    rasterio = None  # type: ignore
    from_bounds = None  # type: ignore
    HAVE_RASTERIO = False

__all__ = [
    "settings",
    "HAVE_RASTERIO",
    "rasterio",
    "from_bounds",
]
