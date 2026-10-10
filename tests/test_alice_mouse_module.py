"""Safe Alice Mouse staging tests. Never touches Android input devices."""

import concurrent.futures
import json
import secrets
import threading
import pytest
from alice_mouse import Command, MouseError, MouseModule, RecordingBackend, RootSocketBackend


@pytest.fixture
def mouse():
    now = [1_000_000_000]
    rendered = []
    hidden = []
    focus = [True]
    backend = RecordingBackend()
    obj = MouseModule(
        backend,
        clock=lambda: now[0],
        focus_ok=lambda: focus[0],
        preview=lambda x, y: rendered.append((x, y)),
        hide=lambda: hidden.append(True),
    )
    obj.start(authorized=True, key=secrets.token_bytes(32))
    return obj, backend, now, rendered, hidden, focus


def run(m, action, x=0, y=0, button=1):
    return m.dispatch(m.issue(Command(action, x, y, button), authorized=True))


@pytest.mark.parametrize(
    "action,args",
    [
        ("move", {"x": 501}),
        ("move", {"y": -501}),
        ("scroll", {"x": 21}),
        ("scroll", {"y": 1}),
        ("down", {"x": 1}),
        ("up", {"button": 3}),
        ("right", {"button": 1}),
        ("click", {"button": 2}),
        ("shell", {}),
        ("move", {"x": True}),
    ],
)
def test_reject_invalid_commands(mouse, action, args):
    m, *_ = mouse
    with pytest.raises(MouseError):
        m.issue(Command(action, **args), authorized=True)


def test_move_click_scroll_right_and_drag(mouse):
    m, b, _, rendered, _, _ = mouse
    assert run(m, "move", 8, -2) == (1178, 538)
    assert run(m, "click", 5, 4) == (1183, 542)
    run(m, "scroll", 3)
    run(m, "right", button=2)
    run(m, "down", button=1)
    run(m, "move", 12, 7)
    run(m, "up", button=1)
    assert [x[0] for x in b.events] == [
        "move",
        "move",
        "down",
        "up",
        "scroll",
        "down",
        "up",
        "down",
        "move",
        "up",
    ]
    assert rendered[-1] == (1195, 549)


def test_untrusted_cannot_issue(mouse):
    m, *_ = mouse
    with pytest.raises(MouseError):
        m.issue(Command("move", 1), authorized=False)
    with pytest.raises(MouseError):
        MouseModule(RecordingBackend()).start(authorized=False, key=secrets.token_bytes(32))


def test_replay_and_out_of_order(mouse):
    m, b, *_ = mouse
    old = m.issue(Command("move", 1), authorized=True)
    newer = m.issue(Command("move", 3), authorized=True)
    m.dispatch(newer)
    with pytest.raises(MouseError):
        m.dispatch(old)
    with pytest.raises(MouseError):
        m.dispatch(newer)
    assert len(b.events) == 1


def test_tampered_mac_denied(mouse):
    m, b, *_ = mouse
    p = json.loads(m.issue(Command("move", 1), authorized=True))
    p["grant"]["x"] = 33
    with pytest.raises(MouseError):
        m.dispatch(json.dumps(p).encode())
    assert b.events == []


def test_expired_and_future_grants_rejected(mouse):
    m, b, now, *_ = mouse
    p = m.issue(Command("move", 1), authorized=True)
    now[0] += 300_000_001
    with pytest.raises(MouseError):
        m.dispatch(p)
    now[0] -= 300_000_002
    with pytest.raises(MouseError):
        m.dispatch(p)
    assert b.events == []


def test_reissue_invalidates_previous_session(mouse):
    m, b, *_ = mouse
    p = m.issue(Command("move", 1), authorized=True)
    m.start(authorized=True, key=secrets.token_bytes(32))
    with pytest.raises(MouseError):
        m.dispatch(p)
    assert b.events == []


def test_concurrent_replay_exactly_once(mouse):
    m, b, *_ = mouse
    packet = m.issue(Command("move", 7), authorized=True)

    def once(_):
        try:
            return m.dispatch(packet) is not None
        except MouseError:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(once, range(10)))
    assert results.count(True) == 1
    assert len(b.events) == 1


def test_focus_gate_denies_and_hides(mouse):
    m, b, _, _, hidden, focus = mouse
    focus[0] = False
    with pytest.raises(MouseError):
        run(m, "move", 10)
    assert b.events == []
    assert hidden


def test_down_auto_releases_on_lease(mouse):
    m, b, now, _, hidden, _ = mouse
    run(m, "down")
    now[0] += 1_500_000_001
    m.tick()
    assert [x[0] for x in b.events] == ["down", "up"]
    assert hidden


@pytest.mark.parametrize("action", ["down", "up", "move", "scroll"])
def test_backend_failure_is_not_silent(mouse, action):
    m, b, *_ = mouse
    if action == "up":
        run(m, "down")
    b.fail_on = action
    with pytest.raises((OSError, MouseError)):
        run(m, action, x=1 if action in ("move", "scroll") else 0)
    if action == "up":
        assert m.active is False
        with pytest.raises(MouseError):
            run(m, "move", 1)


def test_close_releases_buttons(mouse):
    m, b, _, _, hidden, _ = mouse
    run(m, "down", button=2)
    m.close()
    assert b.events[-1][0] == "up"
    assert hidden
    with pytest.raises(MouseError):
        run(m, "move", 1)


def test_resize_invalidates_position_and_hides(mouse):
    m, _, _, _, hidden, _ = mouse
    run(m, "move", 120, 10)
    m.resize(1080, 2340, 1)
    assert (m.cursor.x, m.cursor.y) == (540, 1170)
    assert hidden


def test_physical_transport_is_off_by_default():
    with pytest.raises(MouseError):
        RootSocketBackend("/data/adb/alice-mouse/control.sock")


def test_packet_huge_or_malformed_denied(mouse):
    m, *_ = mouse
    for packet in [b"", b"{" * 2000, b"null", b"[]", b'{"mac":"bad"}']:
        with pytest.raises(MouseError):
            m.dispatch(packet)


def test_no_implicit_admin_from_role_payload(mouse):
    m, b, *_ = mouse
    original = json.loads(m.issue(Command("move", 1), authorized=True))
    original["grant"]["role"] = "admin"
    with pytest.raises(MouseError):
        m.dispatch(json.dumps(original).encode())
    assert not b.events


def test_autonomous_button_release_without_tick():
    import threading
    import time

    backend = RecordingBackend()
    released = threading.Event()
    original_emit = backend.emit

    def observe(action, x=0, y=0, button=1):
        original_emit(action, x, y, button)
        if action == "up":
            released.set()

    backend.emit = observe
    m = MouseModule(backend, focus_ok=lambda: True)
    m.start(authorized=True, key=secrets.token_bytes(32))
    m.dispatch(m.issue(Command("down"), authorized=True))
    assert released.wait(2.5), "held button must release without tick()"
    assert not m.held
    m.close()


def test_close_cancels_autonomous_release():
    backend = RecordingBackend()
    m = MouseModule(backend, focus_ok=lambda: True)
    m.start(authorized=True, key=secrets.token_bytes(32))
    m.dispatch(m.issue(Command("down"), authorized=True))
    m.close()
    assert [event[0] for event in backend.events] == ["down", "up"]
    assert m._lease_timer is None


def test_focus_probe_failure_releases_button_and_revokes_controller(mouse):
    m, backend, _, _, hidden, _ = mouse
    run(m, "down", button=1)

    def failing_probe():
        raise RuntimeError("foreground inspector unavailable")

    m.focus_ok = failing_probe
    try:
        with pytest.raises(MouseError, match="foreground"):
            run(m, "move", x=5)
        assert [event[0] for event in backend.events] == ["down", "up"]
        assert not m.held
        assert m.active is False
        assert hidden
    finally:
        m.close()


def test_focus_probe_truthy_nonbool_must_not_authorize_input(mouse):
    m, backend, _, _, hidden, _ = mouse
    m.focus_ok = lambda: 1
    with pytest.raises(MouseError, match="unsafe foreground"):
        run(m, "move", x=5)
    assert backend.events == []
    assert hidden
    m.close()


def test_unexpected_release_exception_keeps_fail_closed(mouse):
    m, backend, _, _, hidden, _ = mouse
    run(m, "down")
    previous_emit = backend.emit

    def failing_release(action, x=0, y=0, button=1):
        if action == "up":
            raise RuntimeError("injected unexpected driver fault")
        previous_emit(action, x, y, button)

    backend.emit = failing_release
    with pytest.raises(MouseError, match="physical release failed"):
        m.close()
    assert m.active is False
    assert m.held == {1}
    assert hidden


@pytest.mark.parametrize(
    "action,x,y,button,encoded",
    [
        ("down", 0, 0, 1, b"down 1 0\n"),
        ("up", 0, 0, 2, b"up 2 0\n"),
        ("right", 0, 0, 2, b"right 0 0\n"),
        ("scroll", 3, 0, 1, b"scroll 3 0\n"),
        ("move", 8, -3, 1, b"move 8 -3\n"),
    ],
)
def test_opt_in_socket_backend_uses_fake_only(monkeypatch, tmp_path, action, x, y, button, encoded):
    import alice_mouse.core as module

    calls = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def settimeout(self, timeout):
            calls.append(("timeout", timeout))

        def connect(self, path):
            calls.append(("connect", path))

        def sendall(self, data):
            calls.append(("send", data))

        def recv(self, _size):
            return b"OK\n"

    monkeypatch.setattr(module.socket, "socket", lambda *_args: FakeSocket())
    destination = tmp_path / "never-opened.sock"
    RootSocketBackend(destination, enabled=True).emit(action, x, y, button)
    assert ("connect", str(destination)) in calls
    assert ("send", encoded) in calls
    assert ("timeout", 1) in calls


def test_fake_socket_backend_denial_fails_closed(monkeypatch, tmp_path):
    import alice_mouse.core as module

    class DeniedSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def settimeout(self, _timeout):
            pass

        def connect(self, _path):
            pass

        def sendall(self, _data):
            pass

        def recv(self, _size):
            return b"DENIED\n"

    monkeypatch.setattr(module.socket, "socket", lambda *_args: DeniedSocket())
    with pytest.raises(MouseError, match="device rejected command"):
        RootSocketBackend(tmp_path / "fake.sock", enabled=True).emit("move", 1, 2)


def test_bad_geometry_cannot_change_cursor(mouse):
    m, *_ = mouse
    with pytest.raises(MouseError, match="invalid display geometry"):
        m.resize(10, 1080, 0)
    with pytest.raises(MouseError, match="invalid display geometry"):
        m.resize(1080, 2340, True)
    m.close()


def test_old_timer_generation_cannot_release_active_drag(mouse):
    m, backend, *_ = mouse
    run(m, "down")
    m._lease_expired(m._lease_generation - 1)
    assert m.held == {1}
    assert [event[0] for event in backend.events] == ["down"]
    m.close()


def test_signer_without_key_and_packet_size_gate(mouse, monkeypatch):
    import alice_mouse.core as module

    m, backend, *_ = mouse
    m.key = None
    with pytest.raises(MouseError, match="no signing key"):
        m.issue(Command("move", 1), authorized=True)
    m.key = secrets.token_bytes(32)
    monkeypatch.setattr(module, "MAX_PACKET", 1)
    with pytest.raises(MouseError, match="packet too large"):
        m.issue(Command("move", 1), authorized=True)
    assert backend.events == []
    m.close()


def test_unheld_up_and_duplicate_down_release_safely(mouse):
    m, backend, *_ = mouse
    with pytest.raises(MouseError, match="button is not held"):
        run(m, "up")
    run(m, "down")
    with pytest.raises(MouseError, match="button already held"):
        run(m, "down")
    assert [event[0] for event in backend.events] == ["down", "up"]
    assert not m.held
    m.close()
