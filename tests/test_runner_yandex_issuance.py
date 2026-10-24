"""IAM and app transport fixtures; no external requests or model invocation."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("issuer", Path(__file__).parents[1] / "scripts/redmi_issue_yandex_key.py")
issuer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(issuer)

ENV = {"YANDEX_IAM_TOKEN": "synthetic-iam-token", "YANDEX_SERVICE_ACCOUNT_ID": "a" * 20, "YANDEX_FOLDER_ID": "b" * 20}
SECRET, KEY_ID = "synthetic-issued-api-secret", "c" * 20
FP = hashlib.sha256(SECRET.encode()).hexdigest()


class Transport:
    def __init__(self):
        self.calls = []
        self.ready, self.configured = True, False
        self.iam_error = self.delivery_error = None
        self.bad_receipt = False

    def __call__(self, method, url, headers=None, payload=None):
        self.calls.append((method, url, headers, payload))
        if url == issuer.APP + "/issuance-status":
            return {"encryption_ready": self.ready, "configured": self.configured,
                    "credential": {"fingerprint": FP, "provider_key_id": KEY_ID, "project_id": ENV["YANDEX_FOLDER_ID"]} if self.configured else None}
        if url == issuer.IAM + "/serviceAccounts/" + ENV["YANDEX_SERVICE_ACCOUNT_ID"]:
            return {"id": ENV["YANDEX_SERVICE_ACCOUNT_ID"], "folderId": ENV["YANDEX_FOLDER_ID"]}
        if method == "POST" and url == issuer.IAM + "/apiKeys":
            if self.iam_error: raise self.iam_error
            return {"apiKey": {"id": KEY_ID, "expiresAt": payload["expiresAt"],
                              "serviceAccountId": payload["serviceAccountId"], "scopes": payload["scopes"]}, "secret": SECRET}
        if method == "POST" and url == issuer.APP + "/issued":
            if self.delivery_error: raise self.delivery_error
            self.configured = True
            return {"storage_verified": not self.bad_receipt, "fingerprint": FP, "provider_key_id": KEY_ID,
                    "authorization_ok": False, "expires_at": payload["expires_at"]}
        if method == "DELETE" and url == issuer.IAM + "/apiKeys/" + KEY_ID: return {}
        raise AssertionError("Unexpected endpoint; model requests are forbidden")


def test_runner_mints_then_delivers_exact_key_without_model_probe(tmp_path):
    transport = Transport()
    pending = tmp_path / "pending.json"
    result = issuer.issue_and_deliver(ENV, pending, transport)
    assert result["status"] == "stored" and result["model_request_sent"] is False
    assert SECRET not in str(result) and ENV["YANDEX_IAM_TOKEN"] not in str(result)
    posts = [x for x in transport.calls if x[0] == "POST"]
    assert [x[1] for x in posts] == [issuer.IAM + "/apiKeys", issuer.APP + "/issued"]
    assert posts[0][3]["scopes"] == ["yc.ai.foundationModels.execute"]
    assert posts[1][3]["api_key"] == SECRET
    assert posts[1][3]["expires_at"] == posts[0][3]["expiresAt"]
    assert not pending.exists()


@pytest.mark.parametrize("change,code", [("missing", "missing_yandex_iam_configuration"), ("encryption", "app_encryption_not_ready"), ("existing", "provider_already_configured")])
def test_preflight_never_mints_on_missing_prerequisite(tmp_path, change, code):
    transport = Transport()
    if change == "encryption": transport.ready = False
    if change == "existing": transport.configured = True
    with pytest.raises(issuer.Failed, match=code):
        issuer.issue_and_deliver({} if change == "missing" else ENV, tmp_path / "pending.json", transport)
    assert not any(x[1].startswith(issuer.IAM) for x in transport.calls)


def test_iam_rejection_is_not_success(tmp_path):
    transport = Transport(); transport.iam_error = issuer.HTTPFailure(403)
    pending = tmp_path / "pending.json"
    with pytest.raises(issuer.Failed, match="iam_key_issuance_http_403"):
        issuer.issue_and_deliver(ENV, pending, transport)
    assert not pending.exists() and not transport.configured


def test_ambiguous_mint_blocks_duplicate_issue_and_keeps_no_secret(tmp_path):
    transport = Transport(); transport.iam_error = issuer.Failed("timeout")
    pending = tmp_path / "pending.json"
    with pytest.raises(issuer.Failed, match="iam_issuance_outcome_unknown"):
        issuer.issue_and_deliver(ENV, pending, transport)
    count = len(transport.calls)
    with pytest.raises(issuer.Failed, match="previous_issuance_requires_review"):
        issuer.issue_and_deliver(ENV, pending, transport)
    assert len(transport.calls) == count
    assert ENV["YANDEX_IAM_TOKEN"] not in pending.read_text()
    assert pending.stat().st_mode & 0o777 == 0o600


def test_definite_app_rejection_revokes_only_new_key(tmp_path):
    transport = Transport(); transport.delivery_error = issuer.HTTPFailure(409)
    pending = tmp_path / "pending.json"
    with pytest.raises(issuer.Failed, match="app_rejected_key_revoked"):
        issuer.issue_and_deliver(ENV, pending, transport)
    assert transport.calls[-1][0:2] == ("DELETE", issuer.IAM + "/apiKeys/" + KEY_ID)
    assert not pending.exists()


@pytest.mark.parametrize("fault", ["timeout", "receipt"])
def test_uncertain_delivery_never_revokes_possibly_saved_key(tmp_path, fault):
    transport = Transport()
    if fault == "timeout": transport.delivery_error = issuer.Failed("timeout")
    else: transport.bad_receipt = True
    pending = tmp_path / "pending.json"
    with pytest.raises(issuer.Failed): issuer.issue_and_deliver(ENV, pending, transport)
    assert not any(x[0] == "DELETE" for x in transport.calls)
    assert SECRET not in pending.read_text()
    assert KEY_ID in pending.read_text()
