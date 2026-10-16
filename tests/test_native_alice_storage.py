"""Native consumer lifecycle, authority and durable-failure regression checks."""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import base64
import pytest
from flask import Flask
import storage.db as db
from agent_memory.client import close_memory_clients
from kristall import Kristall, KristallError
from alice_printing.binding import printing_store


def test_chat_repository_defaults_and_reset_are_durable(tmp_path, monkeypatch):
    monkeypatch.delenv("ALICE_MEMORY_PATH")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "legacy-name.db"))
    assert db.memory_file_path() == tmp_path / "alice.memory"
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "explicit.memory"))
    assert db.memory_file_path() == tmp_path / "explicit.memory"
    assert db.is_memory_configured()
    db.init_config_table()
    assert db.normalize_conversation_title("") == "Новый чат"
    db.create_conversation("chat", "Chat", "model")
    db.add_message("chat", "user", "value")
    db.set_config("setting", 1)
    db.reset_memory_db()
    close_memory_clients()
    assert db.get_conversations() == []
    assert db.get_messages("chat") == []
    assert db.get_config("setting") is None


def test_native_delete_never_ignores_writer_failure_or_partial_state(tmp_path, monkeypatch):
    path = tmp_path / "values.memory"
    with Kristall(path) as store:
        store.set("saved", 1)
        store.commit()
        with pytest.raises(KristallError, match="prefix"):
            store.items(None)
        store.set("pending", 2)
        with pytest.raises(KristallError, match="idle"):
            store.delete("saved")
        store.commit()
        store.delete("saved")
        assert store.get("saved") is None
        before = store.last_commit
        store.delete("missing")
        assert store.last_commit == before

        def fail(_changes):
            raise MemoryError("injected RAM publication interruption")

        monkeypatch.setattr(store._engine, "_apply_changes", fail)
        store.set("next", 3)
        with pytest.raises(MemoryError):
            store.commit()
        with pytest.raises(KristallError, match="RAM publication"):
            store.items()
        with pytest.raises(KristallError, match="recovery required"):
            store.delete("missing")


def test_department_archive_is_persisted(monkeypatch):
    import organization.departments as departments

    monkeypatch.setenv("ALICE_DEPARTMENTS_ADMIN_TOKEN", "test-admin")
    departments.upsert_department({"id": "test", "name": "Test"})
    app = Flask(__name__)
    app.register_blueprint(departments.departments_bp)
    response = app.test_client().delete(
        "/api/departments/test", headers={"X-Department-Admin-Token": "test-admin"}
    )
    assert response.status_code == 200
    close_memory_clients()
    assert departments.get_department("test")["status"] == "archived"


def test_keys_keep_unique_identity_and_missing_keys_fail(monkeypatch):
    import secret_store.key_manager as keys

    monkeypatch.setenv("ALICE_KEY_MANAGER_KEY", base64.urlsafe_b64encode(b"1" * 32).decode())
    keys.create_schema(db.get_store())
    keys.store_secret(
        secret="test-secret", name="key", purpose="test", provider="local", key_ref="fixed"
    )
    with pytest.raises(keys.KeyManagerError, match="already exists"):
        keys.store_secret(
            secret="replacement", name="key", purpose="test", provider="local", key_ref="fixed"
        )
    assert keys.read_secret("fixed") == "test-secret"
    with pytest.raises(keys.KeyNotFoundError):
        keys.revoke_key("missing")
    with pytest.raises(keys.KeyNotFoundError):
        keys.delete_key("missing")


def test_partner_native_crud_and_status_authority():
    import organization.partner_relations as partners

    ctx = {"_universal_context": {"call": SimpleNamespace(user_id="owner", metadata={})}}
    parent = partners.create_partner({"id": "parent", "name": "Original"}, ctx)
    assert partners.list_partners({"status": "lead"}, ctx)["partners"][0]["id"] == parent["id"]
    partners.update_partner({"partner_id": "parent", "name": "Updated", "status": "active"}, ctx)
    contact = partners.add_contact({"partner_id": "parent", "name": "Ada"}, ctx)["contact"]
    assert (
        partners.list_contacts({"partner_id": "parent"}, ctx)["contacts"][0]["id"] == contact["id"]
    )
    partners.delete_contact({"contact_id": contact["id"]}, ctx)
    assert partners.list_contacts({"partner_id": "parent"}, ctx)["contacts"] == []
    message = partners.prepare_message({"partner_id": "parent", "body": "Draft"}, ctx)["message"]
    assert (
        partners.queue_message({"message_id": message["id"]}, ctx)["delivery"]["status"] == "queued"
    )
    assert partners.get_thread({"partner_id": "parent"}, ctx)["messages"][0]["status"] == "queued"
    follow = partners.create_followup({"partner_id": "parent", "title": "Follow"}, ctx)
    assert partners.complete_followup({"followup_id": follow["id"]}, ctx)["status"] == "completed"
    partners.delete_partner({"partner_id": "parent"}, ctx)
    close_memory_clients()
    assert partners.get_partner({"partner_id": "parent"}, ctx)["partner"]["status"] == "archived"
    with pytest.raises(ValueError, match="already exists"):
        partners.create_partner({"id": "parent", "name": "Duplicate"}, ctx)


def test_partner_changed_or_foreign_records_never_acknowledge_mutation(monkeypatch):
    import organization.partner_relations as partners

    ctx = {"_universal_context": {"call": SimpleNamespace(user_id="owner", metadata={})}}
    partners.create_partner({"id": "parent", "name": "Parent"}, ctx)
    contact = partners.add_contact({"partner_id": "parent", "name": "Ada"}, ctx)["contact"]
    cached = dict(contact)
    db.get_store().delete("partners/contact/" + contact["id"])
    monkeypatch.setattr(partners, "_contact_for_owner", lambda *_args: cached)
    with pytest.raises(ValueError, match="contact not found"):
        partners.delete_contact({"contact_id": contact["id"]}, ctx)
    with pytest.raises(ValueError, match="partner not found"):
        partners._insert_owned("contact", "foreign", {"partner_id": "parent", "owner_id": "other"})
    with pytest.raises(ValueError, match="record not found"):
        partners._update_owned("partner", "parent", "other", {"name": "stolen"})
    msg = partners.prepare_message({"partner_id": "parent", "body": "Draft"}, ctx)["message"]
    key = "partners/message/" + msg["id"]
    with db.get_store().transaction() as tx:
        tx.set(key, {**tx.get(key), "status": "sent"})
    assert partners.queue_message({"message_id": msg["id"]}, ctx)["delivery"]["status"] == "sent"


def test_provider_validation_and_iam_metadata_use_native_store():
    import provider_accounts.credentials as credentials

    store = db.get_store()
    credentials.create_schema(store)
    with pytest.raises(credentials.NoActiveCredentialError):
        credentials.resolve_client_credential(None)
    with pytest.raises(credentials.NoActiveCredentialError):
        credentials.resolve_client_credential(None, store)
    for action in (credentials.bootstrap_credential, credentials.replace_active_credential):
        with pytest.raises(ValueError):
            action(store, "", "", lambda value: value, provider="unknown")
    first = credentials.bootstrap_credential(
        store, "test-secret", "project", lambda value: "cipher", provider="yandex"
    )
    with pytest.raises(credentials.NoActiveCredentialError):
        credentials.bootstrap_credential(store, "other", "project", lambda value: "cipher")
    with pytest.raises(credentials.CredentialError):
        credentials.resolve_client_credential(None, store)
    with pytest.raises(credentials.CredentialError):
        credentials.promote_rotated_key(
            store,
            first.id + 1,
            "cipher",
            "new",
            "project",
            datetime.now(timezone.utc),
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
    with pytest.raises(ValueError):
        credentials.record_health_check(store, "unknown", status="connected")
    with pytest.raises(ValueError):
        credentials.record_health_check(store, "yandex", status="unsupported")
    assert credentials.get_cloudru_iam_credentials(store, lambda value: value) is None
    with pytest.raises(ValueError):
        credentials.save_cloudru_iam_credentials(
            store,
            key_id="",
            key_secret="value",
            project_id="",
            service_account_id=None,
            encrypt=lambda value: value,
        )
    with pytest.raises(ValueError):
        credentials.save_cloudru_iam_credentials(
            store,
            key_id="id",
            key_secret="value",
            project_id="",
            service_account_id=None,
            encrypt=lambda value: "cipher",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    credentials.save_cloudru_iam_credentials(
        store,
        key_id="id",
        key_secret="test-secret",
        project_id="project",
        service_account_id="account",
        encrypt=lambda value: "cipher",
    )
    restored = credentials.get_cloudru_iam_credentials(store, lambda value: "decrypted-test")
    assert restored["key_id"] == "id" and restored["key_secret"] == "decrypted-test"
    assert "test-secret" not in str(store.get("provider/cloudru-iam"))


def test_quotas_require_valid_policy_and_preserve_disabled_authority():
    import provider_accounts.quotas as quotas

    with pytest.raises(ValueError):
        quotas.upsert_policy("", period_seconds=1, max_requests=-1)
    with pytest.raises(ValueError):
        quotas.assign_user_policy("", "")
    quotas.upsert_policy("disabled", period_seconds=60, max_requests=1, enabled=False)
    quotas.assign_user_policy("owner", "disabled")
    assert quotas.reserve_request("owner") is None
    with db.get_store().transaction() as tx:
        tx.delete("quota/policy/disabled")
    with pytest.raises(RuntimeError, match="unavailable"):
        quotas.get_user_policy("owner")


def test_treasury_rejects_invalid_financial_mutation():
    import economy.treasury as treasury

    before = db.get_store().last_commit
    with pytest.raises(ValueError):
        treasury.record_expense("", float("nan"), "invalid")
    assert db.get_store().last_commit == before


def test_budget_controller_hydrates_confirmed_accounts_after_restart():
    from economy.budget_controller import AccountType, BudgetAccount, BudgetLimits, BudgetController
    from economy.budget_repository import BudgetRepository

    def make(kind):
        return BudgetAccount(
            kind,
            "RUB",
            allocated=Decimal("100"),
            limits=BudgetLimits(Decimal("100"), Decimal("10"), Decimal("100")),
        )

    repo = BudgetRepository()
    first = BudgetController(
        "native",
        make(AccountType.REAL),
        make(AccountType.DEMO),
        repository=repo,
        cooldown_seconds=60,
    )
    first.spend(10, account_type=AccountType.REAL)
    close_memory_clients()
    second = BudgetController(
        "native", make(AccountType.REAL), make(AccountType.DEMO), repository=BudgetRepository()
    )
    assert second.snapshot()["accounts"]["REAL"]["spent"] == "10.00"
    assert second.snapshot()["accounts"]["REAL"]["locked_until"] is not None
    assert second.snapshot()["accounts"]["DEMO"]["spent"] == "0.00"


@pytest.mark.parametrize(
    "violation", ["account", "cooldown-argument", "single", "daily", "total", "locked"]
)
def test_native_budget_enforces_authoritative_financial_limits(violation):
    from economy.budget_controller import AccountType, BudgetAccount, BudgetLimits
    from economy.budget_repository import BudgetRepository, BudgetPersistenceError

    repo = BudgetRepository()
    account = BudgetAccount(
        AccountType.REAL,
        "RUB",
        allocated=Decimal("100"),
        limits=BudgetLimits(Decimal("100"), Decimal("100"), Decimal("100")),
    )
    repo.configure("limits", account)
    key = next(iter(db.get_store().items(prefix="budget/account/")))
    with db.get_store().transaction() as tx:
        row = tx.get(key)
        if violation in {"single", "daily", "total"}:
            field = {
                "single": "max_single_operation",
                "daily": "max_daily_loss",
                "total": "max_total_loss",
            }[violation]
            row["limits"][field] = "1"
        if violation == "locked":
            row["locked_until"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        tx.set(key, row)
    before = db.get_store().last_commit
    if violation in {"account", "cooldown-argument"}:
        with pytest.raises(ValueError):
            repo.apply(
                "limits",
                "UNKNOWN" if violation == "account" else "REAL",
                "SPEND",
                2,
                cooldown_seconds=-1 if violation == "cooldown-argument" else 0,
            )
    else:
        with pytest.raises(BudgetPersistenceError):
            repo.apply("limits", "REAL", "SPEND", 2)
    assert db.get_store().last_commit == before


def test_native_budget_daily_rollover_and_cooldown_are_durable():
    from economy.budget_controller import AccountType, BudgetAccount, BudgetLimits
    from economy.budget_repository import BudgetRepository

    repo = BudgetRepository()
    repo.configure(
        "limits",
        BudgetAccount(
            AccountType.REAL,
            "RUB",
            allocated=Decimal("100"),
            limits=BudgetLimits(Decimal("100"), Decimal("2"), Decimal("100")),
        ),
    )
    key = next(iter(db.get_store().items(prefix="budget/account/")))
    with db.get_store().transaction() as tx:
        row = tx.get(key)
        tx.set(
            key,
            {
                **row,
                "loss_period": "2000-01-01",
                "loss_today": "100",
                "locked_until": "2099-01-01T00:00:00+00:00",
            },
        )
    repo.apply("limits", "REAL", "SPEND", 2, cooldown_seconds=60)
    record = db.get_store().get(key)
    assert record["loss_today"] == "2.00"
    assert record["locked_until"] is not None


def test_local_agent_heartbeat_lease_recovery_and_result_authority(monkeypatch):
    import agent_runtime.local_gateway as gateway

    monkeypatch.setenv("ALICE_LOCAL_AGENT_BOOTSTRAP_TOKEN", "bootstrap")
    app = Flask(__name__)
    app.register_blueprint(gateway.local_agent_bp)
    client = app.test_client()
    registration = client.post(
        "/api/local-agents/register",
        json={"agent_id": "agent"},
        headers={"Authorization": "Bearer bootstrap"},
    ).get_json()
    headers = {"Authorization": "Bearer " + registration["token"]}
    assert client.post("/api/local-agents/agent/heartbeat", headers=headers).status_code == 200
    job = gateway.enqueue_local_tool_job("agent", "demo.echo", {})
    assert (
        client.get("/api/local-agents/agent/poll", headers=headers).get_json()["job"]["id"] == job
    )
    key = "local-job/" + job
    with db.get_store().transaction() as tx:
        tx.set(key, {**tx.get(key), "lease_until": 0})
    close_memory_clients()
    assert (
        client.get("/api/local-agents/agent/poll", headers=headers).get_json()["job"]["id"] == job
    )
    url = "/api/local-agents/agent/jobs/missing/result"
    assert client.post(url, json={"status": "completed"}, headers=headers).status_code == 404
    assert (
        client.post(
            "/api/local-agents/missing/jobs/id/result",
            json={"status": "completed"},
            headers=headers,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/local-agents/agent/jobs/" + job + "/result",
            json={"status": "completed"},
            headers={"Authorization": "Bearer invalid"},
        ).status_code
        == 401
    )


def test_provider_clients_use_same_native_credential_writer(monkeypatch):
    from cryptography.fernet import Fernet
    from provider_accounts.credentials import replace_active_credential
    from secret_store.credential_crypto import encrypt_secret
    import provider_accounts.credentials_routes as routes
    from yandex_client_modules.request_mixin import _resolve_global_provider_credential

    monkeypatch.setenv("ALICE_PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    assert routes._load_credential("yandex") is None
    replace_active_credential(
        db.get_store(), "test-native-api-key", "project", encrypt_secret, "yandex"
    )
    assert routes._load_credential("yandex").api_key == "test-native-api-key"
    client = SimpleNamespace(_config=SimpleNamespace(), session=SimpleNamespace(headers={}))
    credential = _resolve_global_provider_credential(client)
    assert credential.api_key == "test-native-api-key"
    assert client.session.headers["OpenAI-Project"] == "project"
    assert db.get_store().info().last_commit is not None


def test_government_rejects_changed_owner_at_publication(monkeypatch):
    import organization.government as government

    monkeypatch.setattr(government, "_current_owner", lambda: "owner")
    case = government.create_case(user_id="owner")
    with db.get_store().transaction() as tx:
        key = "government/case/" + case["case_id"]
        tx.set(key, {**tx.get(key), "user_id": "other"})
    monkeypatch.setattr(government, "get_case", lambda _identity: dict(case))
    before = db.get_store().last_commit
    with pytest.raises(ValueError, match="case not found"):
        government.collect_data(case["case_id"], {"full_name": "Owner"})
    assert db.get_store().last_commit == before


def test_environment_failed_start_is_persisted_and_missing_metadata_is_not_acknowledged(
    monkeypatch,
):
    import remote_runtime.environments as environments

    identity = "test-environment"
    record = {
        "environment_id": identity,
        "branch_name": "master",
        "commit_sha": "a" * 40,
        "status": "STOPPED",
        "owner_id": None,
        "data_namespace": "namespace",
        "runtime_pid": None,
        "runtime_port": None,
        "url": "/test",
        "created_at": 1,
        "updated_at": 1,
        "stopped_at": 1,
        "started_at": None,
        "deleted_at": None,
        "error": None,
    }
    with db.get_store().transaction() as tx:
        tx.set("environment/record/" + identity, record)

    class FailedRuntime:
        def start(self):
            raise RuntimeError("injected runtime start failure")

    monkeypatch.setattr(environments, "EnvironmentRuntime", lambda _record: FailedRuntime())
    with pytest.raises(RuntimeError, match="injected"):
        environments.start_environment(identity)
    close_memory_clients()
    assert db.get_store().get("environment/record/" + identity)["status"] == "FAILED"
    with pytest.raises(KeyError, match="not_found"):
        environments._persist_environment("missing", {"status": "RUNNING"})


def test_concurrent_settlement_does_not_replace_confirmed_financials(monkeypatch):
    import printing3d.service as printing

    order = printing.create_order(printing_store(), "owner", {"title": "Order", "quoted_price": 10})
    printing.settle_order(printing_store(), "owner", order["id"], actual_revenue=10, actual_cost=2)
    real = printing.get_order
    stale = {**real(printing_store(), "owner", order["id"]), "settled_at": None, "status": "ready"}
    monkeypatch.setattr(printing, "get_order", lambda *_args: dict(stale))
    with pytest.raises(ValueError, match="already settled"):
        printing.settle_order(
            printing_store(), "owner", order["id"], actual_revenue=20, actual_cost=2
        )
    assert (
        printing.settle_order(
            printing_store(), "owner", order["id"], actual_revenue=10, actual_cost=2
        )["id"]
        == order["id"]
    )
    with db.get_store().transaction() as tx:
        tx.set(
            "printing/order/" + order["id"],
            {**tx.get("printing/order/" + order["id"]), "status": "cancelled", "settled_at": None},
        )
    with pytest.raises(ValueError, match="cancelled"):
        printing.settle_order(
            printing_store(), "owner", order["id"], actual_revenue=10, actual_cost=2
        )
    db.get_store().delete("printing/order/" + order["id"])
    printing.settle_order(printing_store(), "owner", order["id"], actual_revenue=10, actual_cost=2)
    assert db.get_store().get("printing/order/" + order["id"]) is None


def test_quota_disable_between_policy_read_and_admission_prevents_reservation(monkeypatch):
    import provider_accounts.quotas as quotas

    quotas.upsert_policy("flipped", period_seconds=60, max_requests=1)
    quotas.assign_user_policy("owner", "flipped")
    original = quotas.get_user_policy

    def read_then_disable(user):
        old = original(user)
        key = "quota/policy/flipped"
        with db.get_store().transaction() as tx:
            tx.set(key, {**tx.get(key), "enabled": False})
        return old

    monkeypatch.setattr(quotas, "get_user_policy", read_then_disable)
    assert quotas.reserve_request("owner") is None
    assert db.get_store().get("quota/usage/owner") is None


def test_implicit_startup_never_hides_existing_legacy_authority(tmp_path, monkeypatch):
    monkeypatch.delenv("ALICE_MEMORY_PATH")
    path = tmp_path / "legacy.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    evidence = b"legacy immutable evidence"
    path.write_bytes(evidence)
    with pytest.raises(KristallError, match="explicit verified native activation"):
        db.init_db()
    assert path.read_bytes() == evidence
    assert not path.with_name("alice.memory").exists()
    native = tmp_path / "accepted.memory"
    monkeypatch.setenv("ALICE_MEMORY_PATH", str(native))
    db.set_config("accepted", True)
    assert native.is_file() and path.read_bytes() == evidence
