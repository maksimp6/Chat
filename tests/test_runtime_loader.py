import sys
import threading

import pytest

from runtime import RuntimeDispatcher, RuntimeLoadError, RuntimeLoader


def _write_revision(root, marker):
    root.mkdir()
    (root / "alice_runtime.py").write_text(
        f'''STATE = []
class Application:
    def __init__(self, host):
        self.host = host
    def invoke(self, operation, payload):
        STATE.append(payload["value"])
        return {{"revision": "{marker}", "state": list(STATE), "runtime": self.host.runtime_id}}
def create_runtime(host):
    return Application(host)
''',
        encoding="utf-8",
    )


def test_two_revision_loaders_do_not_share_modules_or_state(tmp_path):
    first_root, second_root = tmp_path / "first", tmp_path / "second"
    _write_revision(first_root, "revision-a")
    _write_revision(second_root, "revision-b")
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a")
    dispatcher.register_runtime("runtime-b")
    before = set(sys.modules)
    first = RuntimeLoader(
        runtime_id="runtime-a", revision="a" * 40, source_root=first_root, dispatcher=dispatcher
    )
    second = RuntimeLoader(
        runtime_id="runtime-b", revision="b" * 40, source_root=second_root, dispatcher=dispatcher
    )
    assert first.load() and second.load()

    barrier = threading.Barrier(2)
    results = {}

    def invoke(name, loader, value):
        barrier.wait()
        results[name] = loader.invoke("record", {"value": value})

    threads = [
        threading.Thread(target=invoke, args=("a", first, "one")),
        threading.Thread(target=invoke, args=("b", second, "two")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == {
        "a": {"revision": "revision-a", "state": ["one"], "runtime": "runtime-a"},
        "b": {"revision": "revision-b", "state": ["two"], "runtime": "runtime-b"},
    }
    assert set(sys.modules) == before


def test_loader_rejects_imports_instead_of_claiming_thread_isolation(tmp_path):
    root = tmp_path / "revision"
    root.mkdir()
    (root / "alice_runtime.py").write_text("import shared_branch_module\n", encoding="utf-8")
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime")
    loader = RuntimeLoader(
        runtime_id="runtime", revision="c" * 40, source_root=root, dispatcher=dispatcher
    )
    with pytest.raises(RuntimeLoadError, match="imports are not supported"):
        loader.load()


def test_host_api_dispatches_through_runtime_dispatcher():
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime")
    dispatcher.register_operation(
        "echo",
        lambda context, payload: {"runtime": context.runtime_id, "payload": dict(payload or {})},
    )
    from runtime import RuntimeHostAPI

    host = RuntimeHostAPI("runtime", dispatcher)
    assert host.dispatch("echo", {"value": 1}) == {
        "runtime": "runtime",
        "payload": {"value": 1},
    }


def test_loader_reports_digest_and_missing_entrypoint(tmp_path):
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime")
    root = tmp_path / "revision"
    root.mkdir()
    loader = RuntimeLoader(
        runtime_id="runtime", revision="d" * 40, source_root=root, dispatcher=dispatcher
    )

    assert loader.source_digest is None
    assert loader.load() is False
    assert loader.loaded is False


def test_loader_rejects_unreadable_entrypoint(tmp_path, monkeypatch):
    root = tmp_path / "revision"
    root.mkdir()
    (root / "alice_runtime.py").write_text("def create_runtime(host): pass\n", encoding="utf-8")
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime")
    loader = RuntimeLoader(
        runtime_id="runtime", revision="e" * 40, source_root=root, dispatcher=dispatcher
    )

    monkeypatch.setattr("importlib.machinery.SourceFileLoader.get_source", lambda self, name: None)
    with pytest.raises(RuntimeLoadError, match="could not be read"):
        loader.load()


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("VALUE = 1\n", "must define create_runtime"),
        (
            "def create_runtime(host):\n    return object()\n",
            "runtime application must define invoke",
        ),
        ("def create_runtime(:\n    pass\n", "invalid runtime entry point"),
        (
            "def create_runtime(host):\n    eval('1')\n",
            "revision call to eval\(\) is not supported",
        ),
    ],
)
def test_loader_rejects_invalid_runtime_contracts(tmp_path, source, message):
    root = tmp_path / "revision"
    root.mkdir()
    (root / "alice_runtime.py").write_text(source, encoding="utf-8")
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime")
    loader = RuntimeLoader(
        runtime_id="runtime", revision="f" * 40, source_root=root, dispatcher=dispatcher
    )

    with pytest.raises(RuntimeLoadError, match=message):
        loader.load()


def test_loader_rejects_invoke_before_load(tmp_path):
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime")
    loader = RuntimeLoader(
        runtime_id="runtime", revision="0" * 40, source_root=tmp_path, dispatcher=dispatcher
    )

    with pytest.raises(RuntimeLoadError, match="revision runtime is not loaded"):
        loader.invoke("noop")
