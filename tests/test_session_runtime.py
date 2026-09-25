import time

from session_runtime import ReadyMadeSession, VirtualLowConsumptionServer, VirtualServerConfig


def test_ready_made_session_clones_all_mutable_configuration():
    original = ReadyMadeSession(
        "template",
        "Developer",
        "developer",
        model="test-model",
        tools=["terminal"],
        environment={"MODE": "dev"},
        files=["README.md"],
        state={"cwd": "/workspace"},
        virtual_server=VirtualServerConfig(cwd="/tmp"),
        template=True,
    )
    clone = original.clone("My Developer")

    assert clone.id != original.id
    assert clone.template is False
    clone.state["cwd"] = "/other"
    clone.environment["MODE"] = "prod"
    clone.tools.append("git")
    clone.files.append("notes.txt")

    assert original.state["cwd"] == "/workspace"
    assert original.environment["MODE"] == "dev"
    assert original.tools == ["terminal"]
    assert original.files == ["README.md"]
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


def test_suspended_runtime_can_restart(tmp_path):
    runtime = VirtualLowConsumptionServer(
        "sess_restart",
        VirtualServerConfig(cwd=str(tmp_path), idle_timeout_seconds=1),
    )
    runtime.start()
    runtime.suspend()
    assert runtime.state == "suspended"

    runtime.start()
    assert runtime.state == "running"


def test_runtime_enforces_timeout(tmp_path):
    runtime = VirtualLowConsumptionServer(
        "sess_timeout",
        VirtualServerConfig(cwd=str(tmp_path), command_timeout_seconds=1),
    )
    result = runtime.execute("sleep 2")
    assert result["timed_out"] is True


def test_runtime_marks_unexpected_failure_and_does_not_restart(monkeypatch, tmp_path):
    runtime = VirtualLowConsumptionServer(
        "sess_failed",
        VirtualServerConfig(cwd=str(tmp_path)),
    )

    def explode(*args, **kwargs):
        raise OSError("runtime backend failed")

    monkeypatch.setattr("session_runtime.subprocess.run", explode)

    try:
        runtime.execute("printf 'boom'")
    except OSError as exc:
        assert str(exc) == "runtime backend failed"
    else:
        raise AssertionError("expected runtime backend failure")

    assert runtime.state == "failed"

    try:
        runtime.start()
    except RuntimeError as exc:
        assert str(exc) == "runtime is in failed state"
    else:
        raise AssertionError("failed runtime must not restart implicitly")


def test_reaper_and_start_are_serialized(tmp_path):
    import threading

    runtime = VirtualLowConsumptionServer(
        "sess_race",
        VirtualServerConfig(cwd=str(tmp_path), idle_timeout_seconds=1),
    )
    runtime.start()
    runtime.mark_idle()

    barrier = threading.Barrier(2)
    results = []

    def reap():
        barrier.wait()
        results.append(runtime.reap_if_idle(time.time() + 2))

    def invoke():
        barrier.wait()
        runtime.start()

    threads = [threading.Thread(target=reap), threading.Thread(target=invoke)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [True] or results == [False]
    assert runtime.state in {"running", "suspended"}
    if runtime.state == "suspended":
        runtime.start()
        assert runtime.state == "running"


def test_ten_concurrent_clones_are_independent():
    from concurrent.futures import ThreadPoolExecutor

    template = ReadyMadeSession(
        "template",
        "Developer",
        "developer",
        tools=["terminal"],
        environment={"MODE": "dev"},
        files=["README.md"],
        state={"cwd": "/workspace"},
        template=True,
    )

    def clone_and_mutate(index):
        clone = template.clone(f"clone-{index}")
        clone.tools.append(f"tool-{index}")
        clone.environment["INDEX"] = str(index)
        clone.state["index"] = index
        return clone

    with ThreadPoolExecutor(max_workers=10) as executor:
        clones = list(executor.map(clone_and_mutate, range(10)))

    assert len({clone.id for clone in clones}) == 10
    assert template.tools == ["terminal"]
    assert template.environment == {"MODE": "dev"}
    assert template.state == {"cwd": "/workspace"}
    assert all(clone.template is False for clone in clones)
