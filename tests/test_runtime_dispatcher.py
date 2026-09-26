import ast
from pathlib import Path

import pytest
import requests

from runtime_dispatcher import RuntimeDispatcher, RuntimeDispatchError


class _Upstream:
    status_code = 206
    headers = {"Content-Type": "text/plain", "Content-Length": "12", "Connection": "close"}

    def __init__(self):
        self.closed = False

    def iter_content(self, chunk_size):
        assert chunk_size == 8192
        yield b"first"
        yield b""
        yield b"second"

    def close(self):
        self.closed = True


class _Transport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def request(self, method, target, **kwargs):
        self.calls.append((method, target, kwargs))
        if self.error:
            raise self.error
        return self.response


def test_dispatch_authorizes_before_contacting_runtime():
    transport = _Transport(_Upstream())
    runtimes = {
        "one": {"owner_id": "alice", "status": "RUNNING", "runtime_port": 4101},
        "two": {"owner_id": "bob", "status": "RUNNING", "runtime_port": 4102},
    }
    dispatcher = RuntimeDispatcher(runtimes.get, transport=transport)

    with pytest.raises(RuntimeDispatchError) as failure:
        dispatcher.dispatch("two", owner_id="alice", method="GET")

    assert (failure.value.code, failure.value.status) == ("environment_not_found", 404)
    assert transport.calls == []


def test_dispatch_scopes_transport_and_preserves_streaming():
    upstream = _Upstream()
    transport = _Transport(upstream)
    dispatcher = RuntimeDispatcher(
        lambda runtime_id: {
            "owner_id": "alice",
            "status": "RUNNING",
            "runtime_port": {"one": 4101, "two": 4102}[runtime_id],
        },
        transport=transport,
    )

    response = dispatcher.dispatch(
        "two",
        owner_id="alice",
        method="POST",
        subpath="api/events",
        query={"cursor": "next"},
        body=b"payload",
        headers={"Host": "public.example", "Authorization": "Bearer scoped"},
        cookies={"session": "scoped"},
    )

    assert transport.calls[0][1] == "http://127.0.0.1:4102/api/events"
    assert transport.calls[0][2]["headers"] == {"Authorization": "Bearer scoped"}
    assert response.status == 206
    assert response.headers == (("Content-Type", "text/plain"),)
    assert b"".join(response.body) == b"firstsecond"
    assert upstream.closed is True


def test_dispatch_returns_safe_error_for_transport_failure():
    transport = _Transport(error=requests.ConnectionError("secret internal endpoint"))
    dispatcher = RuntimeDispatcher(
        lambda _runtime_id: {"status": "RUNNING", "runtime_port": 4101, "owner_id": None},
        transport=transport,
    )

    with pytest.raises(RuntimeDispatchError) as failure:
        dispatcher.dispatch("one", owner_id="alice", method="GET")

    assert (failure.value.code, failure.value.status, str(failure.value)) == (
        "environment_runtime_unreachable",
        502,
        "environment_runtime_unreachable",
    )


def test_preview_route_has_no_direct_runtime_transport():
    source = Path(__file__).resolve().parents[1] / "environment_routes.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "requests" not in imports

    proxy = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_proxy"
    )
    calls = [node for node in ast.walk(proxy) if isinstance(node, ast.Call)]
    assert any(
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "runtime_dispatcher"
        and call.func.attr == "dispatch"
        for call in calls
    )
