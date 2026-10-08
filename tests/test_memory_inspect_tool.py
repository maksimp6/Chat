"""Legacy Alice diagnostic adapter must fail closed without explicit ownership."""

from tool_providers.filesystem import memory_inspect


def test_inspector_requires_client_owned_database():
    assert "error" in memory_inspect({})


def test_inspector_rejects_caller_selected_path_or_namespace():
    for arguments in ({"namespace": "secrets"}, {"db_path": "../private"}, {"limit": 1}):
        assert "error" in memory_inspect(arguments)
