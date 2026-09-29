from unittest.mock import Mock, patch

import runtime_migrations


def test_drop_legacy_invocation_conversation_fk_is_noop_for_sqlite():
    conn = Mock()

    with patch.object(runtime_migrations, "is_postgres_connection", return_value=False):
        runtime_migrations._drop_legacy_invocation_conversation_fk(conn)

    conn.execute.assert_not_called()


def test_drop_legacy_invocation_conversation_fk_drops_postgres_constraints():
    conn = Mock()
    query_result = Mock()
    query_result.fetchall.return_value = [{"name": 'legacy"conversation_fk'}]
    conn.execute.return_value = query_result

    with patch.object(runtime_migrations, "is_postgres_connection", return_value=True):
        runtime_migrations._drop_legacy_invocation_conversation_fk(conn)

    assert conn.execute.call_count == 2
    migration_query = conn.execute.call_args_list[0].args[0]
    assert "pg_constraint" in migration_query
    conn.execute.assert_called_with(
        'ALTER TABLE invocations DROP CONSTRAINT "legacy""conversation_fk"'
    )
