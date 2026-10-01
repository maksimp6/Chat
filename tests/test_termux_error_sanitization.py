"""Security tests for termux_system_tools.py - error message sanitization"""

from pathlib import Path


def test_termux_tools_no_raw_exceptions_in_errors():
    """Test that termux error responses don't return raw exception strings"""
    root = Path(__file__).resolve().parents[1]
    source = (root / "termux_system_tools.py").read_text(encoding="utf-8")

    # Find the _run_termux_cmd function
    start = source.find("def _run_termux_cmd(cmd:")
    assert start >= 0, "_run_termux_cmd function should exist"

    next_def = source.find("\ndef ", start + 1)
    func_body = source[start:next_def] if next_def != -1 else source[start:]

    # Should not return raw exception string
    assert 'return {"success": False, "error": str(e)}' not in func_body, (
        "Error responses should not return raw exception strings"
    )

    # Should use safe error message instead
    assert '"error": "System error"' in func_body, (
        "Should return generic safe error message for unexpected exceptions"
    )


def test_termux_tools_exception_logged_safely():
    """Test that exceptions are logged safely without returning them to client"""
    root = Path(__file__).resolve().parents[1]
    source = (root / "termux_system_tools.py").read_text(encoding="utf-8")

    # Find exception handler
    start = source.find("except Exception as e:")
    assert start >= 0, "Exception handler should exist"

    end = source.find("\ndef ", start)
    if end == -1:
        handler_section = source[start:]
    else:
        handler_section = source[start:end]

    # Should log with exc_info=True (includes traceback)
    assert "exc_info=True" in handler_section, (
        "Exception should be logged with full traceback for debugging"
    )

    # Should NOT include str(e) in the response
    assert 'str(e)' not in handler_section, (
        "Should not include raw exception in response"
    )


def test_termux_tools_timeout_safe_error():
    """Test that timeout errors use safe messages"""
    root = Path(__file__).resolve().parents[1]
    source = (root / "termux_system_tools.py").read_text(encoding="utf-8")

    # Find timeout handler
    start = source.find("except subprocess.TimeoutExpired:")
    assert start >= 0, "Timeout handler should exist"

    end = source.find("\nexcept", start + 1)
    timeout_section = source[start:end]

    # Should return generic message with timeout value, not full exception
    assert "Превышено время ожидания" in timeout_section, (
        "Should return user-friendly timeout message"
    )

    # Should not use str(e)
    assert 'str(e)' not in timeout_section


def test_termux_tools_stderr_truncated():
    """Test that stderr from subprocess is truncated before returning"""
    root = Path(__file__).resolve().parents[1]
    source = (root / "termux_system_tools.py").read_text(encoding="utf-8")

    # Find returncode handling
    start = source.find("if proc.returncode != 0:")
    assert start >= 0, "Return code check should exist"

    end = source.find("\nif not stdout:", start)
    returncode_section = source[start:end]

    # Should truncate stderr to prevent leaking large error messages
    assert "[:200]" in returncode_section or "[:100]" in returncode_section, (
        "stderr should be truncated to limit information disclosure"
    )


def test_termux_tools_no_command_leakage():
    """Test that command arguments are not leaked in error messages"""
    root = Path(__file__).resolve().parents[1]
    source = (root / "termux_system_tools.py").read_text(encoding="utf-8")

    # Find _run_termux_cmd function
    start = source.find("def _run_termux_cmd(cmd:")
    assert start >= 0

    next_def = source.find("\ndef ", start + 1)
    func_body = source[start:next_def] if next_def != -1 else source[start:]

    # Responses should not include the full cmd list
    assert 'str(cmd)' not in func_body, (
        "Command arguments should not be included in error responses"
    )

    # Only binary name should be logged, not full command
    assert 'f"[TERMUX] {binary}' in func_body, (
        "Logging should only include binary name, not full command"
    )
