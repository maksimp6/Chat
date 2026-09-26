"""Provider credential configuration and health-check API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import logging
import uuid

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
from trace_manager import traced_operation
from yandex_api_key_provider import YandexApiKeyProvider
from model_discovery import invalidate_model_discovery_cache


logger = logging.getLogger("alice.provider_credentials")


provider_credentials_bp = Blueprint(
    "provider_credentials",
    __name__,
    url_prefix="/api/provider-credentials",
)

CLOUDRU_DEFAULT_TTL = timedelta(days=1)


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
        logger.warning(
            "Provider credentials request rejected: admin authentication failed "
            "method=%s path=%s remote=%s auth_header_present=%s",
            request.method,
            request.path,
            request.remote_addr,
            bool(request.headers.get("Authorization")),
        )
        return jsonify(
            {
                "error": "provider_credential_admin_authentication_required",
                "detail": (
                    "Request rejected before provider API-key validation. "
                    "Configure ALICE_PROVIDER_CREDENTIALS_TOKEN and send "
                    "Authorization: Bearer <token>, or enable the authenticated "
                    "short-token session."
                ),
            }
        ), 401
    logger.info(
        "Provider credentials admin authentication accepted: method=%s path=%s remote=%s",
        request.method,
        request.path,
        request.remote_addr,
    )
    return None


def _cloudru_ttl() -> timedelta:
    try:
        days = int(os.getenv("CLOUDRU_KEY_TTL_DAYS", "1"))
    except ValueError as exc:
        raise ValueError("CLOUDRU_KEY_TTL_DAYS must be an integer") from exc
    if not 1 <= days <= 365:
        raise ValueError("CLOUDRU_KEY_TTL_DAYS must be between 1 and 365")
    return timedelta(days=days)


def _provider_client(provider: str, project_id: str | None = None):
    if provider == YANDEX:
        if not isinstance(project_id, str) or not project_id.strip():
            raise CredentialError("Yandex project_id is required for provider client")
        return YandexApiKeyProvider(
            project_id=project_id.strip(),
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
            return None
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
            "status": (
                "connected"
                if authorization_ok
                else "invalid"
                if health_status == "invalid"
                else "forbidden"
                if health_status == "forbidden"
                else "configured"
                if health_status == "configured"
                else "checking"
            ),
            "authorization_ok": authorization_ok,
            "error": row["last_check_error"],
            "credential": credential,
            "rotation": {
                "supported": bool(
                    getattr(
                        _provider_client(provider, row["project_id"]),
                        "rotation_supported",
                        lambda _key_id: False,
                    )(credential["provider_key_id"])
                ),
                "due": bool(row["expires_at"] and rotation_needed(row["expires_at"])),
                "active_key_verified": authorization_ok,
            },
            "last_checked_at": str(row["last_checked_at"]) if row["last_checked_at"] else None,
        }

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


def _perform_health_check(provider: str) -> dict:
    credential = _load_credential(provider)
    if credential is None:
        return {"status": "not_configured", "error": None}
    try:
        if provider == YANDEX:
            _provider_client(provider, credential.project_id).validate_key(credential.api_key)
        else:
            _provider_client(provider).validate_key(credential.api_key)
        error = None
        status = "connected"
    except PermissionError:
        logger.exception("Provider health check authorization failed: provider=%s", provider)
        error = "authorization_failed"
        status = "invalid"
    except Exception:
        logger.exception("Provider health check failed: provider=%s", provider)
        error = "provider_unavailable"
        status = "unavailable"

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
@traced_operation("provider_credentials.status_check")
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
        logger.exception("Provider health check request failed")
        return jsonify(
            {
                "error": "health_check_failed",
                "detail": "Проверка провайдера временно недоступна",
            }
        ), 503
    return provider_credentials_status()


@provider_credentials_bp.get("/status")
@traced_operation("provider_credentials.status")
def provider_credentials_status():
    guard = _guard()
    if guard:
        return guard
    return jsonify(
        {
            "providers": [
                _status_for(YANDEX),
                _status_for(CLOUDRU),
            ]
        }
    )


@provider_credentials_bp.post("/cloudru/service-accounts")
@traced_operation("cloudru.service_accounts")
def cloudru_service_accounts():
    guard = _guard()
    if guard:
        return guard
    payload = request.form.to_dict()

    def pick(*names):
        for name in names:
            value = payload.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    key_id = pick("iam_key_id", "keyId", "key_id", "accessKeyId", "access_key_id")
    key_secret = pick(
        "iam_key_secret",
        "secret",
        "keySecret",
        "key_secret",
        "accessKeySecret",
        "access_key_secret",
    )
    if not key_id or not key_secret:
        return jsonify({"error": "IAM JSON must contain Key ID and Key Secret"}), 400
    expires_raw = pick("expiresAt", "expires_at", "keyExpiresAt", "key_expires_at")
    if expires_raw:
        try:
            if datetime.fromisoformat(expires_raw.replace("Z", "+00:00")) <= datetime.now(
                timezone.utc
            ):
                return jsonify({"error": "cloudru_iam_master_key_expired"}), 401
        except ValueError:
            return jsonify({"error": "invalid_cloudru_iam_master_key_expiry"}), 400

    try:
        accounts = CloudRuIamClient(key_id=key_id, key_secret=key_secret).list_service_accounts()
        return jsonify(
            {
                "service_accounts": [
                    {
                        "id": str(item.get("id") or item.get("service_account_id") or ""),
                        "name": str(item.get("name") or ""),
                        "project_id": str(
                            item.get("project_id") or item.get("target", {}).get("project_id") or ""
                        ),
                    }
                    for item in accounts
                ]
            }
        )
    except Exception:
        logger.exception("Cloud.ru service account discovery failed")
        return jsonify(
            {
                "error": "cloudru_service_accounts_failed",
                "detail": "Не удалось получить список service accounts",
            }
        ), 502


@provider_credentials_bp.post("/cloudru/bootstrap")
@traced_operation("cloudru.bootstrap")
def bootstrap_cloudru():
    guard = _guard()
    if guard:
        return guard

    payload = request.form.to_dict()

    def pick(*names):
        for name in names:
            value = payload.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    key_id = pick("iam_key_id", "keyId", "key_id", "accessKeyId", "access_key_id")
    key_secret = pick(
        "iam_key_secret",
        "secret",
        "keySecret",
        "key_secret",
        "accessKeySecret",
        "access_key_secret",
    )
    project_id = request.form.get("project_id", "").strip() or pick("projectId", "project_id")
    requested_sa_id = request.form.get("service_account_id", "").strip() or pick(
        "serviceAccountId", "service_account_id"
    )
    if not key_id or not key_secret:
        return jsonify({"error": "Cloud.ru IAM Key ID and Key Secret are required"}), 400
    if not requested_sa_id:
        return jsonify(
            {
                "error": "service_account_id_required",
                "detail": "Create or select an existing Cloud.ru service account with a project role, then provide its UUID.",
            }
        ), 400
    try:
        service_account_id = str(uuid.UUID(requested_sa_id))
    except (ValueError, AttributeError) as exc:
        return jsonify(
            {
                "error": "invalid_service_account_id",
                "detail": "Cloud.ru service_account_id must be a valid UUID.",
            }
        ), 400

    iam_expires_raw = pick("expiresAt", "expires_at", "keyExpiresAt", "key_expires_at")
    iam_expires_at = None
    if iam_expires_raw:
        try:
            iam_expires_at = datetime.fromisoformat(iam_expires_raw.replace("Z", "+00:00"))
            if iam_expires_at <= datetime.now(timezone.utc):
                return jsonify({"error": "cloudru_iam_master_key_expired"}), 401
        except ValueError:
            return jsonify({"error": "invalid_cloudru_iam_master_key_expiry"}), 400

    try:
        # Static API keys are issued directly for an existing service account.
        # Do not enumerate or auto-create accounts here: the Cloud.ru API documents
        # API-key creation as a separate operation and requires the caller to have
        # the appropriate project role.
        management = CloudRuIamClient(key_id=key_id, key_secret=key_secret)
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
            return jsonify(
                {"error": "Cloud.ru API-key creation did not return key ID and secret"}
            ), 502

        provider = CloudRuApiKeyProvider(base_url=config.CLOUDRU_BASE_URL, iam_client=management)
        provider.validate_key(api_secret)

        conn = get_conn()
        try:
            save_cloudru_iam_credentials(
                conn,
                key_id=key_id,
                key_secret=key_secret,
                project_id=project_id,
                service_account_id=service_account_id,
                encrypt=encrypt_secret,
            )
            replace_active_credential(
                conn,
                api_secret,
                project_id,
                encrypt_secret,
                CLOUDRU,
                provider_key_id=api_key_id,
                ttl=_cloudru_ttl(),
            )
            record_health_check(conn, CLOUDRU, status="connected", error=None)
        finally:
            conn.close()

        return jsonify(
            {
                "provider": CLOUDRU,
                "status": "connected",
                "service_account_id": service_account_id,
                "provider_key_id": api_key_id,
                "rotation": {
                    "supported": True,
                    "ttl_days": int(_cloudru_ttl().total_seconds() / 86400),
                },
            }
        )
    except PermissionError:
        return jsonify({"error": "Cloud.ru Foundation Models authorization failed"}), 401
    except Exception:
        logger.exception("Cloud.ru bootstrap failed")
        return jsonify(
            {
                "error": "cloudru_bootstrap_failed",
                "detail": "Не удалось завершить настройку Cloud.ru",
            }
        ), 502


@provider_credentials_bp.put("")
@traced_operation("provider_credentials.update")
def update_provider_credentials():
    guard = _guard()
    if guard:
        return guard
    try:
        data = request.get_json(silent=False)
    except Exception:
        logger.exception("Invalid provider credentials JSON payload")
        return jsonify(
            {"error": "invalid_json", "detail": "Тело запроса должно содержать корректный JSON"}
        ), 400
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object is required"}), 400

    yandex_api_key = data.get("yandex_api_key")
    yandex_project_id = data.get("yandex_project_id")
    values = (
        (YANDEX, yandex_api_key),
        (CLOUDRU, data.get("cloudru_api_key")),
    )
    supplied = [
        (provider, value.strip())
        for provider, value in values
        if isinstance(value, str) and value.strip()
    ]
    if isinstance(yandex_api_key, str) and yandex_api_key.strip():
        if not isinstance(yandex_project_id, str) or not yandex_project_id.strip():
            return jsonify(
                {
                    "error": "yandex_project_id_required",
                    "provider": YANDEX,
                    "detail": "Yandex Cloud Project ID is required together with the API key.",
                }
            ), 400
        yandex_project_id = yandex_project_id.strip()
    elif isinstance(yandex_project_id, str) and yandex_project_id.strip():
        return jsonify(
            {
                "error": "yandex_api_key_required",
                "provider": YANDEX,
                "detail": "Yandex Cloud API key is required together with the Project ID.",
            }
        ), 400
    if not supplied:
        return jsonify({"error": "Yandex Cloud API key or Cloud.ru API key is required"}), 400
    if any(len(value) > 4096 for _, value in supplied):
        return jsonify({"error": "API key is too long"}), 400

    try:
        for provider, api_key in supplied:
            client = _provider_client(provider, yandex_project_id if provider == YANDEX else None)
            logger.debug("provider credential validation started: provider=%s", provider)
            try:
                client.validate_key(api_key)
            except PermissionError:
                logger.debug(
                    "provider credential validation rejected: provider=%s",
                    provider,
                )
                return jsonify(
                    {
                        "error": "authorization_failed",
                        "provider": provider,
                        "status": "invalid",
                    }
                ), 401
            except Exception:
                logger.exception("provider credential validation failed: provider=%s", provider)
                return jsonify(
                    {
                        "error": "provider_health_check_failed",
                        "provider": provider,
                        "status": "invalid",
                        "detail": "Проверка API key завершилась ошибкой провайдера",
                    }
                ), 502
            else:
                logger.debug("provider credential accepted for storage: provider=%s", provider)

        conn = get_conn()
        try:
            for provider, api_key in supplied:
                replace_active_credential(
                    conn,
                    api_key,
                    yandex_project_id if provider == YANDEX else "",
                    encrypt_secret,
                    provider,
                    provider_key_id=None,
                    ttl=timedelta(hours=12),
                )
                record_health_check(
                    conn,
                    provider,
                    status="connected",
                    error=None,
                )
        finally:
            conn.close()
    except Exception as exc:
        logger.exception(
            "Provider credential storage failed: providers=%s error=%s",
            [provider for provider, _ in supplied],
            exc,
        )
        return jsonify(
            {
                "error": "credential_storage_failed",
                "detail": "Ключ проверен, но сервер не смог сохранить его. Подробности записаны в серверный лог.",
            }
        ), 503

    if any(provider == YANDEX for provider, _ in supplied):
        invalidate_model_discovery_cache()

    return provider_credentials_status()
