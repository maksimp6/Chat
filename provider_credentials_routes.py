"""Provider credential configuration and health-check API."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
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
    get_cloudru_iam_credentials,
    save_cloudru_iam_credentials,
)
from cloudru_api_key_provider import CloudRuApiKeyProvider
from cloudru_iam import CloudRuIamClient
from yandex_api_key_provider import YandexApiKeyProvider


provider_credentials_bp = Blueprint(
    "provider_credentials",
    __name__,
    url_prefix="/api/provider-credentials",
)

CLOUDRU_DEFAULT_TTL = timedelta(days=90)


def _authorized() -> bool:
    """Authorize provider-credential administration without exposing secrets publicly."""
    configured = os.getenv("ALICE_PROVIDER_CREDENTIALS_TOKEN", "").strip()
    if configured:
        auth = request.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        return bool(token) and token == configured

    # When the application's short-token gate is enabled, its before_request
    # middleware has already authenticated this request. Reuse that session
    # rather than introducing a second browser credential.
    if os.getenv("ALICE_REQUIRE_SHORT_TOKEN", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True

    # Fail closed for remote deployments when no explicit admin token is set.
    remote = request.remote_addr or ""
    return remote in {"127.0.0.1", "::1"}


def _guard():
    if not _authorized():
        return jsonify({"error": "provider credential administration authentication required"}), 401
    return None


def _cloudru_ttl() -> timedelta:
    try:
        days = int(os.getenv("CLOUDRU_KEY_TTL_DAYS", "1"))
    except ValueError as exc:
        raise ValueError("CLOUDRU_KEY_TTL_DAYS must be an integer") from exc
    if not 1 <= days <= 365:
        raise ValueError("CLOUDRU_KEY_TTL_DAYS must be between 1 and 365")
    return timedelta(days=days)


def _provider_key_from_environment(provider: str) -> str | None:
    if provider == YANDEX:
        return config.API_KEY
    if provider == CLOUDRU:
        return None
    raise ValueError(f"Unsupported provider: {provider}")


def _provider_key_id_from_environment(provider: str) -> str | None:
    if provider == YANDEX:
        return config.YANDEX_PROVIDER_KEY_ID
    if provider == CLOUDRU:
        return None
    raise ValueError(f"Unsupported provider: {provider}")


def _provider_client(provider: str):
    if provider == YANDEX:
        return YandexApiKeyProvider(
            project_id=config.PROJECT_ID,
            ai_endpoint=config.BASE_URL,
        )
    if provider == CLOUDRU:
        conn = get_conn()
        try:
            management = get_cloudru_iam_credentials(conn, decrypt_secret)
        finally:
            conn.close()
        iam_client = None
        if management:
            iam_client = CloudRuIamClient(key_id=management["key_id"], key_secret=management["key_secret"])
        return CloudRuApiKeyProvider(base_url=config.CLOUDRU_BASE_URL, iam_client=iam_client)
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
                      fingerprint, last_checked_at, last_check_status, last_check_error
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
            "fingerprint": row["fingerprint"],
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
    guard = _guard()
    if guard:
        return guard
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
    guard = _guard()
    if guard:
        return guard
    return jsonify({
        "providers": [
            _status_for(YANDEX),
            _status_for(CLOUDRU),
        ]
    })


@provider_credentials_bp.post("/cloudru/bootstrap")
def bootstrap_cloudru():
    guard = _guard()
    if guard:
        return guard

    upload = request.files.get("iam_json")
    if upload is None:
        return jsonify({"error": "iam_json file is required"}), 400

    try:
        payload = json.load(upload.stream)
    except (ValueError, UnicodeDecodeError):
        return jsonify({"error": "invalid IAM JSON"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "IAM JSON must be an object"}), 400

    def pick(*names):
        for name in names:
            value = payload.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    key_id = pick("keyId", "key_id", "accessKeyId", "access_key_id")
    key_secret = pick("secret", "keySecret", "key_secret", "accessKeySecret", "access_key_secret")
    project_id = request.form.get("project_id", "").strip() or pick("projectId", "project_id")
    requested_sa_id = request.form.get("service_account_id", "").strip() or pick("serviceAccountId", "service_account_id")
    sa_name = request.form.get("service_account_name", "").strip() or "Alice Pro"
    if not key_id or not key_secret:
        return jsonify({"error": "IAM JSON must contain Key ID and Key Secret"}), 400
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400

    try:
        management = CloudRuIamClient(key_id=key_id, key_secret=key_secret)
        accounts = management.list_service_accounts()
        account = None
        if requested_sa_id:
            account = next((item for item in accounts if str(item.get("id") or "") == requested_sa_id), None)
            if account is None:
                return jsonify({"error": "service_account_not_found"}), 404
        else:
            account = next(
                (item for item in accounts
                 if str(item.get("name") or "").strip().lower() == sa_name.lower()
                 and str(item.get("project_id") or item.get("target", {}).get("project_id") or "") == project_id),
                None,
            )
            if account is None:
                account = management.create_service_account(
                    project_id=project_id,
                    name=sa_name,
                    description="Alice Pro Foundation Models runtime",
                )

        service_account_id = str(account.get("id") or account.get("service_account_id") or "")
        if not service_account_id:
            return jsonify({"error": "Cloud.ru did not return service account ID"}), 502

        expires_at = datetime.now(timezone.utc) + _cloudru_ttl()
        key = management.create_api_key(
            service_account_id=service_account_id,
            name="Alice Pro Foundation Models",
            description="Managed by Alice Pro; rotated daily",
            products=["foundation-models"],
            expires_at=expires_at.isoformat().replace("+00:00", "Z"),
        )
        api_key_id = str(key.get("id") or key.get("key_id") or "")
        api_secret = key.get("secret")
        if not api_key_id or not isinstance(api_secret, str) or not api_secret:
            return jsonify({"error": "Cloud.ru API-key creation did not return key ID and secret"}), 502

        provider = CloudRuApiKeyProvider(base_url=config.CLOUDRU_BASE_URL, iam_client=management)
        provider.validate_key(api_secret)

        conn = get_conn()
        try:
            iam_expires_raw = pick("expiresAt", "expires_at", "keyExpiresAt", "key_expires_at")
        iam_expires_at = None
        if iam_expires_raw:
            try:
                iam_expires_at = datetime.fromisoformat(iam_expires_raw.replace("Z", "+00:00"))
                if iam_expires_at <= datetime.now(timezone.utc):
                    return jsonify({"error": "cloudru_iam_master_key_expired"}), 401
            except ValueError:
                return jsonify({"error": "invalid_cloudru_iam_master_key_expiry"}), 400

        save_cloudru_iam_credentials(
                conn, key_id=key_id, key_secret=key_secret,
                project_id=project_id, service_account_id=service_account_id,
                encrypt=encrypt_secret,
            )
            replace_active_credential(
                conn, api_secret, project_id, encrypt_secret, CLOUDRU,
                provider_key_id=api_key_id, ttl=_cloudru_ttl(),
            )
            record_health_check(conn, CLOUDRU, status="connected", error=None)
        finally:
            conn.close()

        return jsonify({
            "provider": CLOUDRU,
            "status": "connected",
            "service_account_id": service_account_id,
            "provider_key_id": api_key_id,
            "rotation": {"supported": True, "ttl_days": int(_cloudru_ttl().total_seconds() / 86400)},
        })
    except PermissionError:
        return jsonify({"error": "Cloud.ru Foundation Models authorization failed"}), 401
    except Exception as exc:
        return jsonify({"error": "cloudru_bootstrap_failed", "detail": str(exc)}), 502


@provider_credentials_bp.put("")
def update_provider_credentials():
    guard = _guard()
    if guard:
        return guard
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object is required"}), 400

    values = (
        (YANDEX, data.get("yandex_api_key")),
    )
    supplied = [(provider, value.strip()) for provider, value in values
                if isinstance(value, str) and value.strip()]
    if not supplied:
        return jsonify({"error": "Use /cloudru/bootstrap for Cloud.ru; only Yandex API key can be entered here"}), 400
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
