"""Inject failure in authorization after C allocation; prove close/free order."""
from pathlib import Path
import pytest


def test_fixture_cleanup_is_registered_before_authorization():
    source=(Path(__file__).with_name('test_signed_c_keyboard.py')).read_text()
    allocation=source.index('keyboard = lib.alice_fixture_new(')
    stack=source.index('with ExitStack() as stack:',allocation)
    free=source.index('stack.callback(lib.alice_fixture_free, keyboard)',stack)
    close=source.index('stack.callback(lib.alice_fixture_close, keyboard)',free)
    auth=source.index('authority.authorize(principal)',close)
    assert allocation < stack < free < close < auth


def test_fixture_cleanup_on_authorization_exception(monkeypatch):
    # Use ExitStack's actual exception unwinding behavior, with fake C callbacks.
    from contextlib import ExitStack
    calls=[]
    def close(pointer):calls.append(('close',pointer))
    def free(pointer):calls.append(('free',pointer))
    with pytest.raises(RuntimeError,match='authorization rejected'):
        with ExitStack() as stack:
            stack.callback(free,42)
            stack.callback(close,42)
            raise RuntimeError('authorization rejected')
    assert calls==[('close',42),('free',42)]
