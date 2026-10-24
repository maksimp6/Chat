"""Runner-side IAM issuance and local app delivery. Never invokes a model."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

IAM = "https://iam.api.cloud.yandex.net/iam/v1"
APP = "http://127.0.0.1:8080/api/provider-credentials/yandex"
SCOPE = "yc.ai.foundationModels.execute"


class Failed(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class HTTPFailure(Failed):
    def __init__(self, status):
        super().__init__("http_" + str(status))
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request_json(method, url, headers=None, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json", **(headers or {}),
    }, method=method)
    # Loopback delivery must not pass through an inherited proxy.
    handlers = [NoRedirect()]
    if url.startswith(APP):
        handlers.append(urllib.request.ProxyHandler({}))
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=30) as response:
            content = response.read(131073)
            if len(content) > 131072:
                raise Failed("response_too_large")
            result = json.loads(content) if content else {}
            if not isinstance(result, dict):
                raise Failed("invalid_response")
            return result
    except urllib.error.HTTPError as exc:
        raise HTTPFailure(exc.code) from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise Failed("request_failed") from None


def issue_and_deliver(env, pending, call=request_json):
    names = ("YANDEX_IAM_TOKEN", "YANDEX_SERVICE_ACCOUNT_ID", "YANDEX_FOLDER_ID")
    if any(not env.get(name, "").strip() for name in names):
        raise Failed("missing_yandex_iam_configuration")
    if any(not re.fullmatch(r"[a-z0-9]{20}", env[name]) for name in names[1:]):
        raise Failed("invalid_yandex_identity")
    if any(c.isspace() for c in env[names[0]]):
        raise Failed("invalid_iam_token")
    if pending.exists():
        raise Failed("previous_issuance_requires_review")
    local_headers = {}
    if env.get("LOGOS_PROVIDER_CREDENTIALS_TOKEN"):
        local_headers["Authorization"] = "Bearer " + env["LOGOS_PROVIDER_CREDENTIALS_TOKEN"]
    status = call("GET", APP + "/issuance-status", local_headers)
    if status.get("encryption_ready") is not True:
        raise Failed("app_encryption_not_ready")
    if status.get("configured") is not False:
        raise Failed("provider_already_configured")
    headers = {"Authorization": "Bearer " + env["YANDEX_IAM_TOKEN"]}
    account = call("GET", IAM + "/serviceAccounts/" + env[names[1]], headers)
    if account.get("id") != env[names[1]] or account.get("folderId") != env[names[2]]:
        raise Failed("service_account_folder_mismatch")
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=12)
    pending.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # A failed/ambiguous mint must not silently create another key on a rerun.
    with pending.open("x") as f:
        os.chmod(pending, 0o600)
        json.dump({"state": "issuance_started", "at": now.isoformat()}, f)
        f.flush(); os.fsync(f.fileno())
    try:
        result = call("POST", IAM + "/apiKeys", headers, {
            "serviceAccountId": env[names[1]], "scopes": [SCOPE],
            "description": "Logos Redmi 9 runner-issued model key",
            "expiresAt": expires.isoformat().replace("+00:00", "Z"),
        })
    except HTTPFailure as exc:
        if exc.status in (400, 401, 403, 404, 429):
            pending.unlink()
        raise Failed("iam_key_issuance_" + exc.code) from None
    except Failed:
        raise Failed("iam_issuance_outcome_unknown") from None
    meta, secret = result.get("apiKey", {}), result.get("secret")
    key_id = meta.get("id")
    if not isinstance(key_id, str) or not re.fullmatch(r"[a-z0-9]{20}", key_id):
        raise Failed("iam_response_missing_key_id")
    with pending.open("w") as f:
        json.dump({"state": "issued_pending_delivery", "provider_key_id": key_id}, f)
        f.flush(); os.fsync(f.fileno())
    if not isinstance(secret, str) or not secret:
        raise Failed("iam_response_missing_secret")
    try:
        actual_expiry = datetime.fromisoformat(meta["expiresAt"].replace("Z", "+00:00"))
        if (meta.get("serviceAccountId") != env[names[1]]
                or meta.get("scopes") != [SCOPE]
                or actual_expiry != expires):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise Failed("iam_key_metadata_mismatch") from None
    payload = {
        "api_key": secret, "project_id": env[names[2]], "provider_key_id": key_id,
        "issued_at": meta.get("createdAt", now.isoformat()),
        "expires_at": meta["expiresAt"],
    }
    fingerprint = hashlib.sha256(secret.encode()).hexdigest()
    try:
        receipt = call("POST", APP + "/issued", local_headers, payload)
    except HTTPFailure as exc:
        if exc.status in (400, 401, 403, 409, 415):
            # These routes reject before writing. Revoke only this newly issued key.
            call("DELETE", IAM + "/apiKeys/" + key_id, headers)
            pending.unlink()
            raise Failed("app_rejected_key_revoked") from None
        raise Failed("app_delivery_outcome_unknown") from None
    except Failed:
        raise Failed("app_delivery_outcome_unknown") from None
    if (receipt.get("storage_verified") is not True
            or receipt.get("fingerprint") != fingerprint
            or receipt.get("provider_key_id") != key_id
            or receipt.get("authorization_ok") is not False):
        raise Failed("app_receipt_mismatch")
    current = call("GET", APP + "/issuance-status", local_headers)
    stored = current.get("credential") or {}
    if (current.get("configured") is not True or stored.get("fingerprint") != fingerprint
            or stored.get("provider_key_id") != key_id
            or stored.get("project_id") != env[names[2]]):
        raise Failed("app_readback_mismatch")
    pending.unlink()
    return {
        "status": "stored", "provider": "yandex", "provider_key_id": key_id,
        "fingerprint": fingerprint, "expires_at": receipt.get("expires_at"),
        "storage_verified": True, "model_request_sent": False,
    }


def main():
    os.umask(0o077)
    pending = Path.home() / "logos-local-state/yandex-issuance.pending.json"
    try:
        result = issue_and_deliver(os.environ, pending)
    except Failed as exc:
        print(json.dumps({"status": "blocked", "error": exc.code, "model_request_sent": False}))
        return 1
    except Exception:
        # Never print a traceback containing request bodies or authentication headers.
        print(json.dumps({"status": "blocked", "error": "issuance_failed", "model_request_sent": False}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
