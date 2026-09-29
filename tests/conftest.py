"""Backend-selection rules for the cross-database CI matrix.

Tests that explicitly replace db.DB_PATH are validating local SQLite isolation
or a SQLite-only filesystem/runtime contract. When the PostgreSQL CI job exports
ALICE_DATABASE_URL globally, that environment setting would otherwise override
the test database path and make unrelated tests share one PostgreSQL database.

Keep those tests on SQLite; all other tests inherit the PostgreSQL URL.
"""

from __future__ import annotations

from pathlib import Path

import pytest


_SQLITE_ISOLATION_MODULES = {
    "test_alice_agent_runner.py",
    "test_chat_sqlite_integration.py",
    "test_chatgpt_mcp.py",
    "test_concurrency_session_recovery.py",
    "test_conversation_titles.py",
    "test_departments.py",
    "test_environment_gateway.py",
    "test_environments.py",
    "test_github_oauth.py",
    "test_government.py",
    "test_key_manager.py",
    "test_memory_api_contract.py",
    "test_observability_migrations.py",
    "test_partner_relations.py",
    "test_persistent_runtime_records.py",
    "test_provider_credentials_routes.py",
    "test_provider_quotas.py",
    "test_runtime_api.py",
    "test_runtime_concurrency.py",
    "test_runtime_lifecycle.py",
    "test_session_profiles.py",
    "test_treasury_billing.py",
    "test_user_identity.py",
}


@pytest.fixture(autouse=True)
def preserve_explicit_sqlite_test_isolation(request, monkeypatch):
    """Do not let PostgreSQL CI override tests that explicitly choose SQLite."""

    if Path(str(request.fspath)).name in _SQLITE_ISOLATION_MODULES:
        monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
