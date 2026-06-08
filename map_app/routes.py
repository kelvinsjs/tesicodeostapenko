"""Application routes for serving pages and APIs."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from .services.dem import (
    SRTMDownloadError,
    build_dem_response,
    prepare_dem_download,
)
from .services.ndvi import (
    NDVIGenerationError,
    build_ndvi_response,
    prepare_ndvi_download,
)
from .services.twi import (
    TWIGenerationError,
    build_twi_response,
    prepare_twi_download,
)
from .services.map_export import (
    ExportResult,
    build_export_response,
    prepare_map_download,
    summarize_area,
)

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    return render_template("map.html")


@bp.route("/check_area", methods=["POST"])
def check_area():
    data = request.get_json(force=True)
    result = summarize_area(
        north=float(data["north"]),
        south=float(data["south"]),
        east=float(data["east"]),
        west=float(data["west"]),
    )
    return jsonify(result)


@bp.route("/download_map", methods=["POST"])
def download_map():
    data = request.get_json(force=True)
    try:
        export: ExportResult = prepare_map_download(
            north=float(data["north"]),
            south=float(data["south"]),
            east=float(data["east"]),
            west=float(data["west"]),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # pragma: no cover - unexpected runtime errors
        return jsonify({"error": str(exc)}), 500

    return build_export_response(export)


@bp.route("/download_ndvi", methods=["POST"])
def download_ndvi():
    data = request.get_json(force=True)
    try:
        export: ExportResult = prepare_ndvi_download(
            north=float(data["north"]),
            south=float(data["south"]),
            east=float(data["east"]),
            west=float(data["west"]),
        )
    except NDVIGenerationError as exc:
        return jsonify({"error": str(exc)}), 500
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # pragma: no cover - unexpected runtime errors
        return jsonify({"error": str(exc)}), 500

    return build_ndvi_response(export)


@bp.route("/download_dem", methods=["POST"])
def download_dem():
    data = request.get_json(force=True)
    try:
        export: ExportResult = prepare_dem_download(
            north=float(data["north"]),
            south=float(data["south"]),
            east=float(data["east"]),
            west=float(data["west"]),
        )
    except SRTMDownloadError as exc:
        return jsonify({"error": str(exc)}), 502
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # pragma: no cover - unexpected runtime errors
        return jsonify({"error": str(exc)}), 500

    return build_dem_response(export)


@bp.route("/download_twi", methods=["POST"])
def download_twi():
    data = request.get_json(force=True)
    try:
        export: ExportResult = prepare_twi_download(
            north=float(data["north"]),
            south=float(data["south"]),
            east=float(data["east"]),
            west=float(data["west"]),
        )
    except TWIGenerationError as exc:
        return jsonify({"error": str(exc)}), 500
    except SRTMDownloadError as exc:
        return jsonify({"error": str(exc)}), 502
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # pragma: no cover - unexpected runtime errors
        return jsonify({"error": str(exc)}), 500

    return build_twi_response(export)
