"""Provider-backed model discovery with a bounded in-process cache."""

from __future__ import annotations
from core.env import env_get

import hashlib
import logging
import threading
import time
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import urlsplit

import requests

from core.config import (
    CLASSIFIER_MODELS,
    EMBEDDING_MODELS,
    IMAGE_MODELS,
    TEXT_MODELS,
    VOICE_MODELS,
    registry_key_for_upstream,
)

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 300
DEFAULT_TIMEOUT_SECONDS = 5


class ModelDiscoveryError(RuntimeError):
    """Raised when the configured provider model catalog cannot be read."""


def _active_provider_credential():
    # The chat resolver is imported only when a request needs auth: importing it
    # while app/model discovery is being initialized would introduce a cycle.
    from core.config import Config
    from yandex_client_modules.request_mixin import _resolve_global_provider_credential

    return _resolve_global_provider_credential(SimpleNamespace(_config=Config))


class ModelDiscovery:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        project_id: str | None = None,
        credential_resolver: Callable[[], Any] | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.project_id = project_id
        self._credential_resolver = credential_resolver
        self.ttl_seconds = max(0, int(ttl_seconds))
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self._lock = threading.Lock()
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._cached_credential: tuple[str, str, str] | None = None

    def get_models(self, *, force_refresh: bool = False) -> dict[str, Any]:
        try:
            # Resolve before consulting the cache so a rotated key or folder
            # cannot reuse another credential's catalog, even within its TTL.
            headers, project_id, credential_identity = self._request_auth()
            now = time.monotonic()
            with self._lock:
                if (
                    not force_refresh
                    and self._cached is not None
                    and self._cached_credential == credential_identity
                    and now - self._cached_at < self.ttl_seconds
                ):
                    return self._copy_catalog(self._cached)
            catalog = self._fetch(headers=headers, project_id=project_id)
        except Exception as exc:
            logger.warning("Model discovery failed: %s", self._safe_error(exc))
            raise ModelDiscoveryError("provider model discovery failed") from exc

        with self._lock:
            self._cached = catalog
            self._cached_at = time.monotonic()
            self._cached_credential = credential_identity
        return self._copy_catalog(catalog)

    def _request_auth(self) -> tuple[dict[str, str], str | None, tuple[str, str, str]]:
        if self._credential_resolver is not None:
            from yandex_client_modules.request_mixin import provider_request_headers

            credential = self._credential_resolver()
            api_key = credential.api_key
            project_id = credential.project_id
            headers = provider_request_headers(credential)
            source = "active_provider_credential"
        else:
            api_key = self.api_key
            project_id = self.project_id
            headers = {"Authorization": "Api-Key " + api_key} if api_key else {}
            source = "explicit"
        if not isinstance(api_key, str) or not api_key:
            raise ModelDiscoveryError("provider credential is not configured")
        if project_id:
            # Models API documents x-project; OpenAI-Project is also sent by its
            # SDK. Both identify the same active credential's folder.
            headers["OpenAI-Project"] = project_id
            headers["x-project"] = project_id
        identity = (source, project_id or "", hashlib.sha256(api_key.encode()).hexdigest())
        return headers, project_id, identity

    def _fetch(self, *, headers: dict[str, str], project_id: str | None) -> dict[str, Any]:
        response = self.session.get(
            f"{self.base_url}/models",
            headers=headers,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            raise ModelDiscoveryError("provider returned an invalid model catalog")

        catalog = static_model_catalog()
        catalog["text"] = {}
        for item in models:
            normalized = self._normalize_model(item, project_id=project_id)
            if normalized is not None:
                catalog[normalized["type"]][normalized["id"]] = normalized

        return catalog

    @staticmethod
    def _normalize_model(item: Any, *, project_id: str | None = None) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        raw_id = item.get("id") or item.get("model") or item.get("name")
        if not isinstance(raw_id, str) or not raw_id.strip():
            return None
        raw_id = raw_id.strip()
        if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in raw_id):
            return None
        if "?" in raw_id or "#" in raw_id:
            return None
        if ":" in raw_id and "://" not in raw_id:
            return None
        model_id = raw_id
        kind = "text"
        if "://" in raw_id:
            try:
                parsed = urlsplit(raw_id)
            except ValueError:
                return None
            kind = {
                "gpt": "text",
                "emb": "embedding",
                "cls": "classifier",
                "art": "image",
            }.get(parsed.scheme)
            if kind is None or not parsed.netloc or "@" in parsed.netloc or ":" in parsed.netloc:
                return None
            if project_id is not None and parsed.netloc != project_id:
                return None
            model_id = parsed.path.removeprefix("/")
        segments = model_id.split("/")
        if not model_id or any(part in {"", ".", ".."} for part in segments[:-1]):
            return None
        if segments[-1] in {".", ".."}:
            return None

        # Models API lists several technologies in one response. A gpt:// URI
        # can also name a Realtime voice model, so scheme alone is insufficient.
        if model_id.startswith(("speech-", "speechkit-", "realtime-")):
            kind = "voice"
        elif model_id.startswith(("text-search-", "text-embeddings")):
            kind = "embedding"
        elif model_id.startswith("classifier-"):
            kind = "classifier"
        elif model_id.startswith(("aliceai-image-", "yandex-art")):
            kind = "image"
        if not segments[-1] and kind != "embedding":
            # The published v2 embeddings URI ends with '/'; preserve it.
            return None
        declared_type = item.get("type") or item.get("model_type")
        if isinstance(declared_type, str):
            if declared_type in {"audio", "voice", "realtime", "stt", "tts"}:
                kind = "voice"
            elif declared_type in {
                "image", "embedding", "classifier", "classification", "embeddings"
            }:
                kind = {"classification": "classifier", "embeddings": "embedding"}.get(
                    declared_type, declared_type
                )

        registries = {
            "text": TEXT_MODELS,
            "voice": VOICE_MODELS,
            "image": IMAGE_MODELS,
            "embedding": EMBEDDING_MODELS,
            "classifier": CLASSIFIER_MODELS,
        }
        registry = registries[kind]
        registry_key = registry_key_for_upstream(model_id) if kind == "text" else None
        if registry_key is None:
            registry_key = next(
                (key for key, info in registry.items() if model_id in {key, info.get("upstream", key)}),
                None,
            )
        existing = registry.get(registry_key, {}) if registry_key is not None else {}
        # Unrecognized versions and tuned IDs retain every URI/path segment.
        # Never reduce them to the base family or silently select its latest.
        registry_key = registry_key or raw_id
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            name = existing.get("name") or model_id
        capabilities = item.get("capabilities")
        if not isinstance(capabilities, dict):
            capabilities = dict(existing.get("capabilities") or {})
        if kind == "voice":
            # The provider's tool support does not install a tool loop in this
            # single-turn audio adapter. Publish its effective capabilities.
            capabilities = {**capabilities, **(existing.get("capabilities") or {})}
        # Photos are accepted only for registry models (chat/mcp_routes.py), so the catalog takes that flag from the registry.
        capabilities = {**capabilities, "multimodal": (existing.get("capabilities") or {}).get("multimodal")}

        result: dict[str, Any] = {
            **deepcopy(existing),
            "name": name,
            "type": kind,
            "capabilities": capabilities,
            "id": registry_key,
            "available": existing.get("available") is not False and item.get("available") is not False,
            "provider_model_id": raw_id,
        }
        if not existing:
            result["pricing_status"] = "unknown"
            if kind != "text":
                # Discovering a private non-text deployment does not install an
                # adapter for it. Keep it visible without advertising execution.
                result["available"] = False
                result["availability_reason"] = "runtime_adapter_not_registered"
        return result

    @staticmethod
    def _copy_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(catalog)

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        # HTTP errors may contain request URLs, headers, or a provider body.
        # The exception class is sufficient for this public catalog fallback.
        return type(exc).__name__


def build_model_discovery() -> ModelDiscovery:
    ttl = int(env_get("MODEL_DISCOVERY_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))
    timeout = float(env_get("MODEL_DISCOVERY_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
    return ModelDiscovery(
        base_url=env_get("YANDEX_BASE_URL", "https://ai.api.cloud.yandex.net/v1"),
        credential_resolver=_active_provider_credential,
        ttl_seconds=ttl,
        timeout_seconds=timeout,
    )


_DEFAULT_DISCOVERY: ModelDiscovery | None = None
_DEFAULT_LOCK = threading.Lock()


def get_model_discovery() -> ModelDiscovery:
    global _DEFAULT_DISCOVERY
    with _DEFAULT_LOCK:
        if _DEFAULT_DISCOVERY is None:
            _DEFAULT_DISCOVERY = build_model_discovery()
        return _DEFAULT_DISCOVERY


def invalidate_model_discovery_cache() -> None:
    discovery = get_model_discovery()
    with discovery._lock:
        discovery._cached = None
        discovery._cached_at = 0.0
        discovery._cached_credential = None


def static_model_catalog() -> dict[str, Any]:
    return {
        "text": {key: dict(value) for key, value in TEXT_MODELS.items()},
        "voice": {key: dict(value) for key, value in VOICE_MODELS.items()},
        "image": {key: dict(value) for key, value in IMAGE_MODELS.items()},
        "embedding": {key: dict(value) for key, value in EMBEDDING_MODELS.items()},
        "classifier": {key: dict(value) for key, value in CLASSIFIER_MODELS.items()},
    }
