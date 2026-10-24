import pytest


@pytest.fixture
def treasury_db(tmp_path, monkeypatch):
    import storage.db as db
    import economy.treasury as treasury
    from agent_memory.client import close_memory_clients

    path = tmp_path / "treasury.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(tmp_path / "logos.memory"))
    monkeypatch.setattr(treasury, "get_store", db.get_store)
    close_memory_clients()
    db.init_db()
    treasury.init_treasury_tables()
    yield path
    close_memory_clients()


def test_calculated_billing_posts_once_and_keeps_trace_reference(treasury_db):
    from economy.billing import settle_billing_to_treasury
    from economy.treasury import demo_top_up, get_account

    owner_id = "user-1"
    demo_top_up(owner_id, 10)
    billing = {
        "cost_status": "calculated",
        "total_cost": 1.25,
        "trace_id": "trace-123",
        "invocation_id": "invocation-123",
    }

    first = settle_billing_to_treasury(billing, owner_id)
    second = settle_billing_to_treasury(billing, owner_id)

    assert first["status"] == "posted"
    assert first["reference"] == "billing:trace-123"
    assert second["status"] == "already_posted"
    account = get_account(owner_id)
    debits = [item for item in account["ledger"] if item["kind"] == "debit"]
    assert len(debits) == 1
    assert debits[0]["reference"] == "billing:trace-123"
    assert account["balance"] == pytest.approx(8.75)


def test_partial_or_unknown_billing_is_never_posted(treasury_db):
    from economy.billing import settle_billing_to_treasury
    from economy.treasury import demo_top_up, get_account

    owner_id = "user-2"
    demo_top_up(owner_id, 10)
    partial = {
        "cost_status": "partial",
        "total_cost": 2,
        "trace_id": "trace-partial",
    }
    unknown = {
        "cost_status": "unknown",
        "total_cost": 2,
        "trace_id": "trace-unknown",
    }

    assert settle_billing_to_treasury(partial, owner_id)["status"] == "skipped"
    assert settle_billing_to_treasury(unknown, owner_id)["status"] == "skipped"
    assert [item for item in get_account(owner_id)["ledger"] if item["kind"] == "debit"] == []


def test_billing_owner_is_bound_to_trusted_owner(treasury_db):
    from economy.billing import settle_billing_to_treasury
    from economy.treasury import demo_top_up

    demo_top_up("trusted-owner", 1)
    billing = {
        "cost_status": "calculated",
        "total_cost": 1,
        "trace_id": "trace-bind-owner",
    }
    result = settle_billing_to_treasury(billing, "trusted-owner")

    assert result["status"] == "posted"
    assert billing["owner_id"] == "trusted-owner"


def test_mismatched_billing_owner_is_rejected(treasury_db):
    from economy.billing import settle_billing_to_treasury

    with pytest.raises(ValueError, match="does not match"):
        settle_billing_to_treasury(
            {
                "cost_status": "calculated",
                "total_cost": 1,
                "trace_id": "trace-wrong-owner",
                "owner_id": "attacker-owner",
            },
            "trusted-owner",
        )


def test_missing_owner_identity_is_safe_skip(treasury_db):
    from economy.billing import settle_billing_to_treasury

    result = settle_billing_to_treasury(
        {"cost_status": "calculated", "total_cost": 1, "trace_id": "trace-missing-owner"},
        None,
    )

    assert result == {"status": "skipped", "reason": "owner_identity_missing"}


def test_insufficient_balance_does_not_create_debit(treasury_db):
    from economy.billing import settle_billing_to_treasury
    from economy.treasury import demo_top_up, get_account

    owner_id = "user-3"
    demo_top_up(owner_id, 1)

    with pytest.raises(ValueError, match="insufficient balance"):
        settle_billing_to_treasury(
            {"cost_status": "calculated", "total_cost": 2, "trace_id": "trace-low-balance"},
            owner_id,
        )

    assert [item for item in get_account(owner_id)["ledger"] if item["kind"] == "debit"] == []


def test_invocation_user_id_flows_into_trace_billing_context(treasury_db):
    from invocation.manager import create_invocation
    from invocation.trace import create_invocation_trace
    from sessions.manager import create_session

    session = create_session()
    context = create_invocation(
        session["id"],
        "conversation-identity",
        user_id="user-4",
    )
    trace = create_invocation_trace(context)
    billing = trace.finalize()["billing"]

    assert context.user_id == "user-4"
    assert billing["owner_id"] == "user-4"
    assert trace.finalize()["context"]["owner_id"] == "user-4"


def test_local_identity_is_not_a_visitor_credential(treasury_db, monkeypatch):
    from app import app
    from economy.treasury_identity import get_current_owner_id

    monkeypatch.setenv("LOGOS_OWNER_ID", "shared")
    with app.test_request_context("/api/treasury/account?owner_id=old", json={"owner_id": "old"}):
        assert get_current_owner_id() == "shared"


def test_web_treasury_opens_without_bootstrap(treasury_db):
    from app import app
    response = app.test_client().get("/api/treasury/account")
    assert response.status_code == 200
    assert response.get_json()["owner_id"]
    assert "Set-Cookie" not in response.headers


def test_bootstrap_returns_local_id_without_token(treasury_db):
    from app import app
    client = app.test_client()
    bootstrap = client.post("/api/users/bootstrap", json={})
    assert bootstrap.status_code == 200
    identity = bootstrap.get_json()
    assert identity["user_id"]
    assert identity["status"] == "local"
    assert "auth_token" not in identity
    assert "Set-Cookie" not in bootstrap.headers
    assert client.get("/api/treasury/account").get_json()["owner_id"] == identity["user_id"]


def test_obsolete_owner_token_does_not_block_treasury(treasury_db):
    from app import app
    response = app.test_client().get("/api/treasury/account", headers={"X-Logos-User-Token": "obsolete"})
    assert response.status_code == 200


def test_existing_ledgers_are_discoverable_and_separately_readable(treasury_db):
    from app import app
    from economy.treasury import demo_top_up
    demo_top_up("old-a", 16)
    demo_top_up("old-b", 7)
    client = app.test_client()
    accounts = client.get("/api/treasury/accounts").get_json()["accounts"]
    assert {row["owner_id"] for row in accounts} == {"old-a", "old-b"}
    first = client.get("/api/treasury/account?owner_id=old-a").get_json()
    second = client.get("/api/treasury/account?owner_id=old-b").get_json()
    assert first["balance"] == 16
    assert second["balance"] == 7
    assert {row["owner_id"] for row in first["ledger"]} == {"old-a"}
