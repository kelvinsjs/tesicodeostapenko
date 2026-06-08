"""Application entrypoint."""
from __future__ import annotations

from map_app import create_app
from map_app.config import settings

app = create_app()


if __name__ == "__main__":
    print("[INFO] Starting Flask app")
    print(f"[INFO] DPI={settings.dpi}, Scale=1:{settings.scale}, MAX_AREA_KM2={settings.max_area_km2}")
    app.run(debug=True)
