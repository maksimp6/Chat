"""Offline regression tests pinning the Logos default title of Termux notifications."""

from tool_providers import termux, termux_system


def _record_commands(monkeypatch, module):
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return {"ok": True}

    monkeypatch.setattr(module, "_run_termux_cmd", fake_run)
    return calls


def test_termux_notification_defaults_title_to_logos(monkeypatch):
    calls = _record_commands(monkeypatch, termux)

    result = termux.termux_notification({"content": "hi"}, {})

    assert result == {"ok": True}
    assert calls == [["termux-notification", "--title", "Logos", "--content", "hi"]]


def test_send_notification_defaults_title_to_logos(monkeypatch):
    calls = _record_commands(monkeypatch, termux_system)

    result = termux_system.send_notification({"content": "hi"})

    assert result == {"ok": True}
    assert calls == [["termux-notification", "--title", "Logos", "--content", "hi"]]
