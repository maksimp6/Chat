from tests.validate_runtime_modules import validate_source


def errors_for(source: str) -> list[str]:
    return validate_source(source, "runtime/example.py")


def test_direct_database_import_is_rejected():
    errors = errors_for("from db import get_conn\nget_conn()\n")
    assert any("direct import from db is forbidden" in error for error in errors)
    assert any("direct get_conn() access is forbidden" in error for error in errors)


def test_direct_network_access_is_rejected():
    errors = errors_for("import requests\nrequests.get('https://example.test')\n")
    assert any("direct import of requests is forbidden" in error for error in errors)
    assert any("direct requests.get() access is forbidden" in error for error in errors)


def test_direct_process_access_is_rejected():
    errors = errors_for("import subprocess\nsubprocess.Popen(['python', 'app.py'])\n")
    assert any("direct import of subprocess is forbidden" in error for error in errors)
    assert any("direct subprocess.Popen() access is forbidden" in error for error in errors)


def test_direct_filesystem_access_is_rejected():
    errors = errors_for("open('shared.txt').read()\n")
    assert any("direct open() access is forbidden" in error for error in errors)


def test_direct_tool_registry_access_is_rejected():
    errors = errors_for("from tool_registry import registry\nregistry.get('x')\n")
    assert any("direct import from tool_registry is forbidden" in error for error in errors)


def test_dispatcher_access_is_allowed():
    errors = errors_for(
        "from runtime import RuntimeDispatcher\n"
        "dispatcher = RuntimeDispatcher()\n"
        "dispatcher.dispatch('a', 'files.read', {'path': 'x'})\n"
    )
    assert errors == []


def test_syntax_errors_are_reported():
    errors = errors_for("def broken(:\n    pass\n")
    assert any("Python syntax error" in error for error in errors)
