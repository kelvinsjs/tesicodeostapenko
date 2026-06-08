"""Flask application factory."""
from __future__ import annotations

import os
from flask import Flask

from .config import settings
from .routes import bp


def create_app() -> Flask:
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    template_dir = os.path.join(base_dir, "templates")
    app = Flask(__name__, template_folder=template_dir)
    app.register_blueprint(bp)

    @app.context_processor
    def inject_settings():  # pragma: no cover - template helper
        return {
            'max_area_km2': settings.max_area_km2,
            'dpi': settings.dpi,
            'scale': settings.scale,
        }

    return app


__all__ = ["create_app"]
