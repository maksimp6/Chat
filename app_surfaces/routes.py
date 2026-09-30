"""HTTP API for the App Surface Broker."""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from app_surfaces.registry import (
    SurfaceAuthError,
    SurfaceNotFound,
    SurfaceValidationError,
    surface_registry,
)


app_surfaces_bp = Blueprint("app_surfaces", __name__, url_prefix="/api/app-surfaces")


def _producer_token() -> str:
    return request.headers.get("X-Alice-Surface-Token", "").strip()


def _error(exc: Exception):
    if isinstance(exc, SurfaceNotFound):
        return jsonify({"error": str(exc), "code": "SURFACE_NOT_FOUND"}), 404
    if isinstance(exc, SurfaceAuthError):
        return jsonify({"error": str(exc), "code": "SURFACE_AUTH_FAILED"}), 403
    return jsonify({"error": str(exc), "code": "SURFACE_INVALID"}), 400


@app_surfaces_bp.post("")
def register_surface():
    data = request.get_json(silent=True) or {}
    try:
        surface, token = surface_registry.register(
            title=data.get("title"),
            source=data.get("source"),
            width=data.get("width"),
            height=data.get("height"),
        )
    except SurfaceValidationError as exc:
        return _error(exc)
    return jsonify({"surface": surface, "producer_token": token}), 201


@app_surfaces_bp.get("")
def list_surfaces():
    return jsonify({"surfaces": surface_registry.list()})


@app_surfaces_bp.delete("/<surface_id>")
def unregister_surface(surface_id: str):
    try:
        surface_registry.unregister(surface_id, _producer_token())
    except (SurfaceNotFound, SurfaceAuthError) as exc:
        return _error(exc)
    return jsonify({"status": "deleted"})


@app_surfaces_bp.put("/<surface_id>/frame")
def publish_surface_frame(surface_id: str):
    frame = request.get_data(cache=False)
    try:
        surface = surface_registry.publish_frame(
            surface_id,
            _producer_token(),
            frame,
            request.mimetype or "",
        )
    except (SurfaceNotFound, SurfaceAuthError, SurfaceValidationError) as exc:
        return _error(exc)
    return jsonify({"surface": surface})


@app_surfaces_bp.get("/<surface_id>/frame")
def get_surface_frame(surface_id: str):
    try:
        frame, content_type, version = surface_registry.frame(surface_id)
    except SurfaceNotFound as exc:
        return _error(exc)

    etag = f'"surface-{surface_id}-{version}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers={"ETag": etag})

    response = Response(frame, mimetype=content_type)
    response.headers["Cache-Control"] = "no-store"
    response.headers["ETag"] = etag
    response.headers["X-Alice-Surface-Version"] = str(version)
    return response


@app_surfaces_bp.post("/<surface_id>/input")
def queue_surface_input(surface_id: str):
    data = request.get_json(silent=True) or {}
    try:
        event = surface_registry.queue_input(surface_id, data)
    except (SurfaceNotFound, SurfaceValidationError) as exc:
        return _error(exc)
    return jsonify({"event": event}), 202


@app_surfaces_bp.get("/<surface_id>/input")
def drain_surface_inputs(surface_id: str):
    raw_limit = request.args.get("limit", "64")
    try:
        limit = int(raw_limit)
    except ValueError:
        return jsonify({"error": "limit must be an integer", "code": "SURFACE_INVALID"}), 400
    try:
        events = surface_registry.drain_inputs(surface_id, _producer_token(), limit=limit)
    except (SurfaceNotFound, SurfaceAuthError) as exc:
        return _error(exc)
    return jsonify({"events": events})
