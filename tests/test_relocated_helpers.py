"""Exercise relocated helpers against a synthetic checkout, without external services."""

import importlib.util
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPERS = (
    "analyze_logs.sh",
    "analyze_modules.sh",
    "crl.sh",
    "debug.sh",
    "file.sh",
    "install_protobuf_env.sh",
    "test_api.sh",
    "view_logs.sh",
    "view_module_logs.sh",
)


@pytest.mark.parametrize("name", HELPERS)
def test_relocated_shell_helper_syntax(name):
    result = subprocess.run(
        ["bash", "-n", str(ROOT / "scripts" / name)], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("name", "args", "stdin", "expected"),
    (
        ("analyze_logs.sh", [], "", "Анализ завершен"),
        ("analyze_modules.sh", [], "", "Анализ завершен"),
        ("debug.sh", ["logs"], "", "CHECKOUT_SENTINEL"),
        ("view_logs.sh", [], "0\n", "Доступные лог-файлы"),
        ("view_module_logs.sh", [], "all\n", "CHECKOUT_SENTINEL"),
        ("file.sh", [], "", "project_dump.txt"),
    ),
)
def test_relocated_helpers_use_checkout_paths_from_unrelated_cwd(
    tmp_path, name, args, stdin, expected
):
    checkout = tmp_path / "checkout with spaces"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / name, scripts / name)
    logs = checkout / "logs"
    logs.mkdir()
    for log in ("app", "api_debug", "voice", "chat", "database", "search"):
        (logs / f"{log}.txt").write_text("CHECKOUT_SENTINEL\n", encoding="utf-8")
    other = tmp_path / "unrelated"
    other.mkdir()
    result = subprocess.run(
        ["bash", str(scripts / name), *args],
        cwd=other,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    assert "Папка logs не найдена" not in result.stdout
    if name == "file.sh":
        assert "CHECKOUT_SENTINEL" in (checkout / "project_dump.txt").read_text()
        assert not (other / "project_dump.txt").exists()


def test_relocated_log_collector_reads_checkout_not_caller(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    source = scripts / "send_logs.py"
    shutil.copy2(ROOT / "scripts" / "send_logs.py", source)
    logs = checkout / "execution_logs"
    logs.mkdir()
    (logs / "trace.json").write_text('{"origin": "checkout"}', encoding="utf-8")
    other = tmp_path / "unrelated"
    (other / "execution_logs").mkdir(parents=True)
    (other / "execution_logs" / "trace.json").write_text('{"origin": "caller"}')
    monkeypatch.chdir(other)
    spec = importlib.util.spec_from_file_location("relocated_send_logs", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.collect_logs() == [{"origin": "checkout"}]
