"""Inject failure in authorization after C allocation; prove close/free order."""

from pathlib import Path
import pytest




def test_fixture_cleanup_on_authorization_exception(monkeypatch):
    # Use ExitStack's actual exception unwinding behavior, with fake C callbacks.
    from contextlib import ExitStack

    calls = []

    def close(pointer):
        calls.append(("close", pointer))

    def free(pointer):
        calls.append(("free", pointer))

    with pytest.raises(RuntimeError, match="authorization rejected"):
        with ExitStack() as stack:
            stack.callback(free, 42)
            stack.callback(close, 42)
            raise RuntimeError("authorization rejected")
    assert calls == [("close", 42), ("free", 42)]
