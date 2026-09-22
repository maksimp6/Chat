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
    fingerprint_key,
    record_health_check,
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
                fingerprint=fingerprint_key(key),
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
    """Return metadata and cached health. Never decrypt a secret here."""
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT id, provider, provider_key_id, yandex_key_id,
                      project_id, issued_at, expires_at, status,
                      last_checked_at, last_check_status, last_check_error
               FROM provider_credentials
               WHERE provider = ? AND status = 'active'
               LIMIT 1""",
            (provider,),
        ).fetchone()
    finally:
        conn.close()

    if row:
        credential = {
            "configured": True,
            "fingerprint": None,
            "provider_key_id": row["provider_key_id"] or row["yandex_key_id"],
            "issued_at": str(row["issued_at"]) if row["issued_at"] else None,
            "expires_at": str(row["expires_at"]) if row["expires_at"] else None,
        }
        health_status = row["last_check_status"] or "unknown"
        authorization_ok = health_status == "connected"
        return {
            "provider": provider,
            "status": "connected" if authorization_ok else (
                "invalid" if health_status == "invalid" else "checking"
            ),
            "authorization_ok": authorization_ok,
            "error": row["last_check_error"],
            "credential": credential,
            "rotation": {
                "supported": bool(
                    getattr(
                        _provider_client(provider),
                        "rotation_supported",
                        lambda _key_id: False,
                    )(credential["provider_key_id"])
                ),
                "due": bool(
                    row["expires_at"] and rotation_needed(row["expires_at"])
                ),
                "active_key_verified": authorization_ok,
            },
            "last_checked_at": str(row["last_checked_at"])
                if row["last_checked_at"] else None,
        }

    env_key = _provider_key_from_environment(provider)
    if not env_key:
        return {
            "provider": provider,
            "status": "not_configured",
            "authorization_ok": False,
            "error": None,
            "credential": _metadata(None),
            "rotation": {
                "supported": False,
                "due": False,
                "active_key_verified": False,
            },
            "last_checked_at": None,
        }

    fingerprint = fingerprint_key(env_key)
    key_id = _provider_key_id_from_environment(provider)
    client = _provider_client(provider)
    supported = bool(
        getattr(client, "rotation_supported", lambda _key_id: False)(key_id)
    )
    return {
        "provider": provider,
        "status": "checking",
        "authorization_ok": False,
        "error": None,
        "credential": {
            "configured": True,
            "fingerprint": fingerprint,
            "provider_key_id": key_id,
            "issued_at": None,
            "expires_at": None,
        },
        "rotation": {
            "supported": supported,
            "due": False,
            "active_key_verified": False,
        },
        "last_checked_at": None,
    }


def _perform_health_check(provider: str) -> dict:
    credential = _load_credential(provider)
    if credential is None:
        return {"status": "not_configured", "error": None}
    try:
        _provider_client(provider).validate_key(credential.api_key)
    except PermissionError:
        error = "unauthorized"
        status = "invalid"
    except Exception:
        error = "provider_unavailable"
        status = "unavailable"
    else:
        error = None
        status = "connected"

    conn = get_conn()
    try:
        record_health_check(
            conn,
            provider,
            status=status,
            error=error,
        )
    finally:
        conn.close()
    return {"status": status, "error": error}


@provider_credentials_bp.post("/status/check")
def provider_credentials_status_check():
    data = request.get_json(silent=True) or {}
    requested = data.get("provider")
    providers = [requested] if requested else [YANDEX, CLOUDRU]
    results = {}
    try:
        for provider in providers:
            if provider not in (YANDEX, CLOUDRU):
                return jsonify({"error": "unsupported_provider"}), 400
            results[provider] = _perform_health_check(provider)
    except Exception:
        return jsonify({"error": "health_check_failed"}), 503
    return provider_credentials_status()


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
                record_health_check(
                    conn,
                    provider,
                    status="connected",
                    error=None,
                )
        finally:
            conn.close()
    except Exception:
        return jsonify({"error": "credential_storage_failed"}), 503

    return provider_credentials_status()
