import pytest


@pytest.fixture
def treasury_db(tmp_path, monkeypatch):
    import db
    import invocation_manager
    import runtime_migrations
    import treasury

    path = tmp_path / "treasury.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setattr(invocation_manager, "get_conn", db.get_conn)
    monkeypatch.setattr(runtime_migrations, "get_conn", db.get_conn)
    monkeypatch.setattr(treasury, "get_conn", db.get_conn)

    db.init_db()
    runtime_migrations.init_runtime_tables()
    treasury.init_treasury_tables()
    return path


def test_calculated_billing_posts_once_and_keeps_trace_reference(treasury_db):
    from billing import settle_billing_to_treasury
    from treasury import demo_top_up, get_account

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
    from billing import settle_billing_to_treasury
    from treasury import demo_top_up, get_account

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


def test_missing_owner_identity_is_safe_skip(treasury_db):
    from billing import settle_billing_to_treasury

    result = settle_billing_to_treasury(
        {"cost_status": "calculated", "total_cost": 1, "trace_id": "trace-missing-owner"},
        None,
    )

    assert result == {"status": "skipped", "reason": "owner_identity_missing"}


def test_insufficient_balance_does_not_create_debit(treasury_db):
    from billing import settle_billing_to_treasury
    from treasury import demo_top_up, get_account

    owner_id = "user-3"
    demo_top_up(owner_id, 1)

    with pytest.raises(ValueError, match="insufficient balance"):
        settle_billing_to_treasury(
            {"cost_status": "calculated", "total_cost": 2, "trace_id": "trace-low-balance"},
            owner_id,
        )

    assert [item for item in get_account(owner_id)["ledger"] if item["kind"] == "debit"] == []


def test_invocation_user_id_flows_into_trace_billing_context(treasury_db):
    from invocation_manager import create_invocation
    from invocation_trace import create_invocation_trace
    from session_manager import create_session

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


def test_owner_identity_does_not_accept_client_body(treasury_db, monkeypatch):
    from app import app

    monkeypatch.delenv("ALICE_OWNER_ID", raising=False)
    app.config.update(TESTING=True)
    with app.test_request_context(
        "/api/treasury/account?owner_id=attacker",
        method="GET",
        json={"owner_id": "attacker"},
    ):
        from treasury_identity import TreasuryIdentityError, get_current_owner_id

        with pytest.raises(TreasuryIdentityError):
            get_current_owner_id()
