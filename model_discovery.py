"""Provider-backed model discovery with a bounded in-process cache."""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import requests

from config import TEXT_MODELS, VOICE_MODELS

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 300
DEFAULT_TIMEOUT_SECONDS = 5


class ModelDiscoveryError(RuntimeError):
    """Raised when the configured provider model catalog cannot be read."""


class ModelDiscovery:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.ttl_seconds = max(0, int(ttl_seconds))
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self._lock = threading.Lock()
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0

    def get_models(self, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if (
                not force_refresh
                and self._cached is not None
                and now - self._cached_at < self.ttl_seconds
            ):
                return self._copy_catalog(self._cached)

        try:
            catalog = self._fetch()
        except Exception as exc:
            logger.warning("Model discovery failed: %s", self._safe_error(exc))
            raise ModelDiscoveryError("provider model discovery failed") from exc

        with self._lock:
            self._cached = catalog
            self._cached_at = time.monotonic()
        return self._copy_catalog(catalog)

    def _fetch(self) -> dict[str, Any]:
        if not self.api_key:
            raise ModelDiscoveryError("provider credential is not configured")

        response = self.session.get(
            f"{self.base_url}/models",
            headers={"Authorization": "Api-Key " + self.api_key},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            raise ModelDiscoveryError("provider returned an invalid model catalog")

        text_models: dict[str, dict[str, Any]] = {}
        for item in models:
            normalized = self._normalize_model(item)
            if normalized is not None:
                text_models[normalized["id"]] = normalized

        if not text_models:
            raise ModelDiscoveryError("provider returned an empty model catalog")

        return {"text": text_models, "voice": dict(VOICE_MODELS)}

    @staticmethod
    def _normalize_model(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        raw_id = item.get("id") or item.get("model") or item.get("name")
        if not isinstance(raw_id, str) or not raw_id.strip():
            return None
        raw_id = raw_id.strip()
        model_id = raw_id
        if raw_id.startswith("gpt://"):
            parts = raw_id.rstrip("/").split("/")
            if len(parts) >= 2:
                model_id = parts[-2]
        elif "/" in raw_id:
            model_id = raw_id.rstrip("/").split("/")[-1]
        model_id = model_id.strip()
        if not model_id:
            return None

        existing = TEXT_MODELS.get(model_id, {})
        name = item.get("name") or existing.get("name") or model_id
        capabilities = item.get("capabilities")
        if not isinstance(capabilities, dict):
            capabilities = dict(existing.get("capabilities") or {})

        result: dict[str, Any] = {
            "name": name,
            "type": "text",
            "capabilities": capabilities,
            "id": model_id,
            "available": True,
        }
        for key in ("input", "cached", "tool", "output"):
            if key in existing:
                result[key] = existing[key]
        return result

    @staticmethod
    def _copy_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
        return {
            "text": {key: dict(value) for key, value in catalog["text"].items()},
            "voice": {key: dict(value) for key, value in catalog["voice"].items()},
        }

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        message = str(exc)
        for marker in ("Api-Key ", "Bearer "):
            if marker in message:
                return message.split(marker, 1)[0] + marker + "<redacted>"
        return message[:300]


def build_model_discovery() -> ModelDiscovery:
    ttl = int(os.getenv("MODEL_DISCOVERY_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))
    timeout = float(os.getenv("MODEL_DISCOVERY_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
    return ModelDiscovery(
        base_url=os.getenv("YANDEX_BASE_URL", "https://ai.api.cloud.yandex.net/v1"),
        api_key=os.getenv("YANDEX_API_KEY") or os.getenv("YC_API_KEY"),
        ttl_seconds=ttl,
        timeout_seconds=timeout,
    )


def static_model_catalog() -> dict[str, Any]:
    return {
        "text": {key: dict(value) for key, value in TEXT_MODELS.items()},
        "voice": {key: dict(value) for key, value in VOICE_MODELS.items()},
    }
