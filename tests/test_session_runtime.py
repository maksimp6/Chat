import time

from session_runtime import (
    ReadyMadeSession,
    VirtualLowConsumptionServer,
    VirtualServerConfig,
)


def test_ready_made_session_clones_without_shared_state():
    original = ReadyMadeSession(
        "template",
        "Developer",
        "developer",
        model="test-model",
        tools=["terminal"],
        state={"cwd": "/workspace"},
        virtual_server=VirtualServerConfig(cwd="/tmp"),
        template=True,
    )
    clone = original.clone("My Developer")

    assert clone.id != original.id
    assert clone.name == "My Developer"
    assert clone.template is False
    clone.state["cwd"] = "/other"
    assert original.state["cwd"] == "/workspace"
    assert clone.virtual_server is not original.virtual_server


def test_runtime_is_lazy_and_tracks_lifecycle(tmp_path):
    runtime = VirtualLowConsumptionServer(
        "sess_test",
        VirtualServerConfig(cwd=str(tmp_path), idle_timeout_seconds=1),
    )
    assert runtime.state == "stopped"
    result = runtime.execute("printf 'hello'")
    assert result["exit_code"] == 0
    assert result["stdout"] == "hello"
    assert runtime.state == "running"
    runtime.mark_idle()
    assert runtime.state == "idle"
    assert runtime.reap_if_idle(time.time() + 2) is True
    assert runtime.state == "suspended"


def test_runtime_enforces_timeout(tmp_path):
    runtime = VirtualLowConsumptionServer(
        "sess_timeout",
        VirtualServerConfig(cwd=str(tmp_path), command_timeout_seconds=1),
    )
    result = runtime.execute("sleep 2")
    assert result["timed_out"] is True
