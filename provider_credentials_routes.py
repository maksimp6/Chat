"""Provider credential configuration and health-check API."""
from __future__ import annotations

from datetime import timedelta
import os

from flask import Blueprint, jsonify, request

from config import config
from credential_crypto import decrypt_secret, encrypt_secret
from db import get_conn
from provider_credentials import (
    CLOUDRU,
    YANDEX,
    ProviderCredential,
    CredentialError,
    NoActiveCredentialError,
    get_active_credential,
    replace_active_credential,
    rotation_needed,
)
from cloudru_api_key_provider import CloudRuApiKeyProvider
from yandex_api_key_provider import YandexApiKeyProvider


provider_credentials_bp = Blueprint(
    "provider_credentials",
    __name__,
    url_prefix="/api/provider-credentials",
)

CLOUDRU_DEFAULT_TTL = timedelta(days=90)


def _cloudru_ttl() -> timedelta:
    try:
        days = int(os.getenv("CLOUDRU_KEY_TTL_DAYS", "90"))
    except ValueError as exc:
        raise ValueError("CLOUDRU_KEY_TTL_DAYS must be an integer") from exc
    if not 1 <= days <= 365:
        raise ValueError("CLOUDRU_KEY_TTL_DAYS must be between 1 and 365")
    return timedelta(days=days)


def _provider_key_from_environment(provider: str) -> str | None:
    if provider == YANDEX:
        return config.API_KEY
    if provider == CLOUDRU:
        return config.CLOUDRU_API_KEY
    raise ValueError(f"Unsupported provider: {provider}")


def _provider_key_id_from_environment(provider: str) -> str | None:
    if provider == YANDEX:
        return config.YANDEX_PROVIDER_KEY_ID
    if provider == CLOUDRU:
        return config.CLOUDRU_API_KEY_ID
    raise ValueError(f"Unsupported provider: {provider}")


def _provider_client(provider: str):
    if provider == YANDEX:
        return YandexApiKeyProvider(
            project_id=config.PROJECT_ID,
            ai_endpoint=config.BASE_URL,
        )
    if provider == CLOUDRU:
        return CloudRuApiKeyProvider(base_url=config.CLOUDRU_BASE_URL)
    raise ValueError(f"Unsupported provider: {provider}")


def _load_credential(provider: str) -> ProviderCredential | None:
    conn = get_conn()
    try:
        try:
            return get_active_credential(
                conn,
                decrypt_secret,
                provider=provider,
            )
        except NoActiveCredentialError:
            key = _provider_key_from_environment(provider)
            if not key:
                return None
            return ProviderCredential(
                api_key=key,
                provider=provider,
                project_id=config.PROJECT_ID if provider == YANDEX else "",
                provider_key_id=_provider_key_id_from_environment(provider),
                fingerprint=None,
            )
    finally:
        conn.close()


def _metadata(credential: ProviderCredential | None) -> dict:
    if credential is None:
        return {
            "configured": False,
            "fingerprint": None,
            "provider_key_id": None,
            "issued_at": None,
            "expires_at": None,
        }
    return {
        "configured": True,
        "fingerprint": credential.fingerprint,
        "provider_key_id": credential.trace_key_id,
        "issued_at": credential.issued_at.isoformat() if credential.issued_at else None,
        "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
    }


def _status_for(provider: str) -> dict:
    try:
        credential = _load_credential(provider)
    except CredentialError:
        return {
            "provider": provider,
            "status": "storage_error",
            "authorization_ok": False,
            "rotation": {
                "supported": False,
                "due": False,
                "active_key_verified": False,
            },
            "credential": {
                "configured": True,
                "fingerprint": None,
                "provider_key_id": None,
                "issued_at": None,
                "expires_at": None,
            },
        }

    if credential is None:
        return {
            "provider": provider,
            "status": "not_configured",
            "authorization_ok": False,
            "rotation": {
                "supported": False,
                "due": False,
                "active_key_verified": False,
            },
            "credential": _metadata(None),
        }

    client = _provider_client(provider)
    try:
        client.validate_key(credential.api_key)
        authorization_ok = True
        status = "connected"
        error = None
    except PermissionError:
        authorization_ok = False
        status = "invalid"
        error = "unauthorized"
    except Exception:
        authorization_ok = False
        status = "invalid"
        error = "provider_unavailable"

    expires_at = credential.expires_at
    due = bool(expires_at and rotation_needed(expires_at))
    supported = bool(
        getattr(client, "rotation_supported", lambda _key_id: False)(
            credential.provider_key_id
        )
    )

    return {
        "provider": provider,
        "status": status,
        "authorization_ok": authorization_ok,
        "error": error,
        "credential": _metadata(credential),
        "rotation": {
            "supported": supported,
            "due": due,
            # The live health check is the verification that the active key
            # currently works. Rotation itself also validates before promotion.
            "active_key_verified": authorization_ok,
        },
    }


@provider_credentials_bp.get("/status")
def provider_credentials_status():
    return jsonify({
        "providers": [
            _status_for(YANDEX),
            _status_for(CLOUDRU),
        ]
    })


@provider_credentials_bp.put("")
def update_provider_credentials():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object is required"}), 400

    values = (
        (YANDEX, data.get("yandex_api_key")),
        (CLOUDRU, data.get("cloudru_api_key")),
    )
    supplied = [(provider, value.strip()) for provider, value in values
                if isinstance(value, str) and value.strip()]
    if not supplied:
        return jsonify({"error": "At least one provider API key is required"}), 400
    if any(len(value) > 4096 for _, value in supplied):
        return jsonify({"error": "API key is too long"}), 400

    try:
        for provider, api_key in supplied:
            client = _provider_client(provider)
            try:
                client.validate_key(api_key)
            except PermissionError:
                return jsonify({
                    "error": "authorization_failed",
                    "provider": provider,
                    "status": "invalid",
                }), 401
            except Exception:
                return jsonify({
                    "error": "provider_health_check_failed",
                    "provider": provider,
                    "status": "invalid",
                }), 502

        conn = get_conn()
        try:
            for provider, api_key in supplied:
                ttl = _cloudru_ttl() if provider == CLOUDRU else None
                replace_active_credential(
                    conn,
                    api_key,
                    config.PROJECT_ID if provider == YANDEX else "",
                    encrypt_secret,
                    provider,
                    provider_key_id=_provider_key_id_from_environment(provider),
                    ttl=ttl or timedelta(hours=12),
                )
        finally:
            conn.close()
    except Exception:
        return jsonify({"error": "credential_storage_failed"}), 503

    return provider_credentials_status()
