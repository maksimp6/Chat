"""RED-first contract for retiring ALICE_DB_BACKEND after durable Memory DB lands.

The switch must not decide whether Alice uses SQL or Memory DB. Keep this
contract in the same functional delivery as the durable engine; until then
these tests deliberately expose the incomplete cutover.
"""

import inspect

import db


def test_backend_selection_does_not_depend_on_legacy_switch(monkeypatch):
    """Both legacy values must select the same file-native backend."""
    results = []
    for value in ("memory", "postgres"):
        monkeypatch.setenv("ALICE_DB_BACKEND", value)
        results.append(db.is_memory_configured())
    assert results == [True, True]


def test_backend_selection_has_no_legacy_environment_switch():
    """Remove the obsolete toggle from the backend selector implementation."""
    assert "ALICE_DB_BACKEND" not in inspect.getsource(db.is_memory_configured)
