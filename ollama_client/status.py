"""What ⚙️ shows on the asmrun tab: the settings and what the runner reports right now."""

from __future__ import annotations

from typing import Any

import requests

from ollama_client import settings

__all__ = ["status"]


def _get(path: str, *, json: Any = None) -> tuple[Any, str | None]:
    url = settings.url().rstrip("/") + path
    try:
        resp = (
            requests.post(url, json=json, timeout=5) if json is not None else requests.get(url, timeout=5)
        )
        resp.raise_for_status()
        return resp.json(), None
    except (requests.RequestException, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def status() -> dict[str, Any]:
    """The settings, the server's version, the loaded models and each model's parameters."""
    result: dict[str, Any] = {"settings": settings.describe(), "errors": {}}
    version, error = _get("/api/version")
    result["version"] = (version or {}).get("version") if isinstance(version, dict) else None
    if error:
        result["errors"]["version"] = error
    running, error = _get("/api/ps")
    result["running"] = [
        {
            "name": model.get("name"),
            "size": model.get("size"),
            "size_vram": model.get("size_vram"),
            "context_length": model.get("context_length"),
            "expires_at": model.get("expires_at"),
        }
        for model in ((running or {}).get("models") or [] if isinstance(running, dict) else [])
        if isinstance(model, dict)
    ]
    if error:
        result["errors"]["ps"] = error
    result["models"] = {}
    for key, info in settings.MODELS.items():
        shown, error = _get("/api/show", json={"model": info["upstream"]})
        details = shown.get("details") if isinstance(shown, dict) else None
        result["models"][key] = {
            "upstream": info["upstream"],
            "parameters": shown.get("parameters") if isinstance(shown, dict) else None,
            "details": details if isinstance(details, dict) else None,
        }
        if error:
            result["errors"][key] = error
    return result
