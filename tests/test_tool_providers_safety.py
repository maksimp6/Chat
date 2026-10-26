import stat

from tool_providers import filesystem
from tool_providers.termux_system import SYSTEM_TOOLS


def _unrestricted(monkeypatch, tmp_path):
    monkeypatch.setattr(filesystem, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(filesystem, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(filesystem, "restrictions_lifted", lambda: True)


def test_write_file_keeps_the_mode_of_the_replaced_file(monkeypatch, tmp_path):
    _unrestricted(monkeypatch, tmp_path)
    script = tmp_path / "run.sh"
    script.write_text("echo old\n")
    script.chmod(0o755)

    result = filesystem.write_file({"path": str(script), "content": "echo new\n"})

    assert "error" not in result
    assert stat.S_IMODE(script.stat().st_mode) == 0o755


def test_delete_path_removes_the_symlink_not_its_target(monkeypatch, tmp_path):
    _unrestricted(monkeypatch, tmp_path)
    target = tmp_path / "important.txt"
    target.write_text("keep")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    result = filesystem.delete_path({"path": str(link)})

    assert "error" not in result
    assert target.read_text() == "keep"
    assert not link.is_symlink()


def test_zip_compress_refuses_to_write_over_its_source(monkeypatch, tmp_path):
    _unrestricted(monkeypatch, tmp_path)
    source = tmp_path / "data.txt"
    source.write_text("precious")

    result = filesystem.zip_compress({"source_path": str(source), "archive_path": str(source)})

    assert "error" in result
    assert source.read_text() == "precious"


def test_sms_camera_contacts_and_wifi_are_not_registered_as_system_tools():
    for name in ("list_sms", "send_sms", "take_camera_photo", "list_contacts", "get_wifi_status"):
        assert name not in SYSTEM_TOOLS
    assert "get_system_battery" in SYSTEM_TOOLS
