import tempfile
from pathlib import Path

from validate_frontend_modules import validate_file


def errors_for(source: str) -> list[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".js", dir=".", delete=False, encoding="utf-8") as handle:
        handle.write(source)
        path = Path(handle.name)
    try:
        return validate_file(path)
    finally:
        path.unlink()


def test_infinite_loops_are_rejected():
    errors = errors_for("while (true) { console.log('never'); }")
    assert any("unbounded while(true)" in error for error in errors)


def test_unbounded_while_is_rejected():
    errors = errors_for("while (ready) { work(); }")
    assert any("while-loop has no statically visible bound" in error for error in errors)


def test_network_inside_loop_requires_cache():
    errors = errors_for("for (let i = 0; i < 10; i += 1) { fetch('/api/data'); }")
    assert any("network request inside loop" in error for error in errors)


def test_large_array_literal_is_rejected():
    source = "const items = [" + ",".join("0" for _ in range(4097)) + "];"
    errors = errors_for(source)
    assert any("array safety violation" in error for error in errors)


def test_large_array_constructor_is_rejected():
    errors = errors_for("const items = new Array(4097);")
    assert any("array safety violation" in error for error in errors)


def test_repeated_lookup_without_cache_is_rejected():
    errors = errors_for("fetch('/api/models'); fetch('/api/models');")
    assert any("cache safety violation" in error for error in errors)


def test_repeated_lookup_with_cache_signal_is_allowed():
    errors = errors_for("const cache = new Map(); fetch('/api/models'); fetch('/api/models');")
    assert not any("cache safety violation" in error for error in errors)


def test_direct_timers_are_rejected():
    errors = errors_for("setTimeout(function() {}, 100);")
    assert any("timer safety violation" in error for error in errors)


def test_delay_and_sleep_are_rejected():
    errors = errors_for("delay(100); sleep(100);")
    assert sum("timer safety violation" in error for error in errors) == 2


def test_direct_transport_access_is_rejected():
    errors = errors_for("fetch('/api/chat');")
    assert any("dispatcher safety violation" in error for error in errors)


def test_dispatcher_transport_is_allowed():
    errors = errors_for("window.AliceDispatcher.request('/api/chat');")
    assert not any("dispatcher safety violation" in error for error in errors)
