import tempfile
from pathlib import Path

import pytest

from validate_frontend_modules import MAX_REPEAT_RATIO, MAX_REPEAT_RUN, ROOT, validate_file


def errors_for(source: str) -> list[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".js", dir=".", delete=False, encoding="utf-8") as handle:
        handle.write(source)
        path = Path(handle.name)
    try:
        return validate_file(path)
    finally:
        path.unlink()


def test_infinite_loops_are_rejected():
    errors = errors_for("while (true) { console.log('never'); }")
    assert any("unbounded while(true)" in error for error in errors)


def test_unbounded_while_is_rejected():
    errors = errors_for("while (ready) { work(); }")
    assert any("while-loop has no statically visible bound" in error for error in errors)


def test_network_inside_loop_requires_cache():
    errors = errors_for("for (let i = 0; i < 10; i += 1) { fetch('/api/data'); }")
    assert any("network request inside loop" in error for error in errors)


def test_large_array_literal_is_rejected():
    source = "const items = [" + ",".join("0" for _ in range(4097)) + "];"
    errors = errors_for(source)
    assert any("array safety violation" in error for error in errors)


def test_large_array_constructor_is_rejected():
    errors = errors_for("const items = new Array(4097);")
    assert any("array safety violation" in error for error in errors)


def test_repeated_lookup_without_cache_is_rejected():
    errors = errors_for("fetch('/api/models'); fetch('/api/models');")
    assert any("cache safety violation" in error for error in errors)


def test_repeated_lookup_with_cache_signal_is_allowed():
    errors = errors_for("const cache = new Map(); fetch('/api/models'); fetch('/api/models');")
    assert not any("cache safety violation" in error for error in errors)


def repetition_errors(source: str) -> list[str]:
    return [error for error in errors_for(source) if "suspicious repeated source text" in error]


def test_independent_functions_do_not_fail_for_repeated_closing_braces():
    source = "\n".join(
        f"function handler{i}() {{\n  return {i};\n}}" for i in range(MAX_REPEAT_RUN + 4)
    )
    assert not repetition_errors(source)


def test_formatter_delimiters_do_not_inflate_repeat_ratio():
    source = "\n".join(
        f"const items{i} = [\n  {{\n    value: {i},\n  }},\n];" for i in range(6)
    )
    assert not repetition_errors(source)


def test_scattered_common_statement_is_not_a_consecutive_run():
    source = "\n".join(
        f"function handler{i}() {{\n  record({i});\n  return null;\n}}"
        for i in range(MAX_REPEAT_RUN + 4)
    )
    assert not repetition_errors(source)


def test_boot_source_has_no_repetition_false_positive():
    errors = validate_file(ROOT / "static" / "boot.js")
    assert not [error for error in errors if "suspicious repeated source text" in error]


@pytest.mark.parametrize("padding", ["", "\n", "  "])
def test_consecutive_statement_payload_is_rejected(padding):
    source = (f"{padding}record('repeated');\n" * MAX_REPEAT_RUN) + "\n".join(
        f"const unique{i} = {i};" for i in range(100)
    )
    assert repetition_errors(source)


def test_repeated_multiline_blocks_are_rejected():
    source = "\n".join(
        "if (ready) {\n  record('first');\n  record('second');\n}" for _ in range(6)
    )
    assert repetition_errors(source)


def test_delimiters_cannot_dilute_repeated_statement_payload():
    source = "\n".join("{\n{\n{\nrecord('same');\n}\n}\n}" for _ in range(8))
    assert repetition_errors(source)


def test_short_empty_source_is_allowed():
    assert not repetition_errors("")
    assert not repetition_errors("\n \n")
    assert not repetition_errors("(function () {\n})();\n")


def test_repetition_limits_are_not_raised():
    assert MAX_REPEAT_RUN == 12
    assert MAX_REPEAT_RATIO == 0.35


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    [
        ("eval('work()');", "obfuscated construct"),
        ("const data = '" + "A" * 4100 + "';", "base64/data payload"),
        ("const text = '\x00';", "control-byte payload"),
    ],
)
def test_payload_protections_remain_enabled(source, diagnostic):
    assert any(diagnostic in error for error in errors_for(source))


def test_valid_deep_nesting_does_not_become_a_payload_run():
    source = "{\n" * 16 + "const marker = 1;\n" + "}\n" * 16
    assert not repetition_errors(source)


def test_run_just_below_limit_is_allowed_when_ratio_is_low():
    source = "record('same');\n" * (MAX_REPEAT_RUN - 1) + "\n".join(
        f"const unique{i} = {i};" for i in range(100)
    )
    assert not repetition_errors(source)


@pytest.mark.parametrize(("repeat_count", "rejected"), [(8, False), (9, True)])
def test_ratio_boundary_uses_only_substantive_lines(repeat_count, rejected):
    source = "\n".join(
        ["record('same');"] * repeat_count
        + [f"record({i});" for i in range(20 - repeat_count)]
    )
    assert bool(repetition_errors(source)) is rejected


@pytest.mark.parametrize("statement", ["counter++;", "value = {};", "'{}';", "// repeat"])
def test_operators_literals_and_comments_are_not_treated_as_delimiters(statement):
    assert repetition_errors((statement + "\n") * MAX_REPEAT_RUN)
