"""Regression tests for the JavaScript/Java/Kotlin exception-handling ratchet (#1288)."""

from collections import Counter

from scripts.check_exception_handling import (
    Report,
    compare,
    load_baseline,
    main,
    scan_repository,
    scan_source,
)


def test_silent_empty_js_catch_is_counted():
    counts = scan_source("try { load(); } catch (e) {}")
    assert counts.try_blocks == 1
    assert counts.silent_catches == 1


def test_catch_with_only_a_comment_is_still_silent():
    counts = scan_source("try { load(); } catch (e) { // ignore for now\n }")
    assert counts.silent_catches == 1


def test_catch_that_reports_the_error_is_not_silent():
    counts = scan_source("try { load(); } catch (e) { console.error(e); }")
    assert counts.silent_catches == 0


def test_optional_binding_catch_is_counted():
    assert scan_source("try { a(); } catch { }").silent_catches == 1


def test_kotlin_underscore_catch_is_counted():
    source = "try { read() } catch (_: IOException) {\n}\n"
    counts = scan_source(source)
    assert counts.try_blocks == 1
    assert counts.silent_catches == 1


def test_nested_braces_inside_a_handler_are_not_silent():
    source = "try { a(); } catch (e) { if (ok) { report(e); } }"
    assert scan_source(source).silent_catches == 0


def test_promise_catch_with_empty_handler_is_counted():
    assert scan_source("p.catch(function () {});").silent_catches == 1


def test_try_and_catch_text_inside_strings_and_comments_are_ignored():
    source = (
        'const s = "try { x } catch (e) {}";\n'
        "// try { y } catch (e) {}\n"
        "/* try { z } catch (e) {} */\n"
        "const t = 'it\\'s fine';\n"
    )
    counts = scan_source(source)
    assert counts.try_blocks == 0
    assert counts.silent_catches == 0


def test_url_inside_string_does_not_hide_following_code():
    source = 'const u = "https://example.test";\ntry { a(); } catch (e) {}\n'
    counts = scan_source(source)
    assert counts.try_blocks == 1
    assert counts.silent_catches == 1


def test_compare_fails_when_a_new_file_adds_a_handler():
    report = Report(try_blocks=Counter({"static/new.js": 1}))
    problems = compare(report, {"try_blocks": {}, "silent_catches": {}})
    assert problems == ["try_blocks grew in static/new.js: 0 -> 1"]


def test_compare_fails_when_an_existing_file_adds_a_silent_catch():
    report = Report(silent_catches=Counter({"static/a.js": 2}))
    problems = compare(report, {"try_blocks": {}, "silent_catches": {"static/a.js": 1}})
    assert problems == ["silent_catches grew in static/a.js: 1 -> 2"]


def test_compare_requires_baseline_to_be_lowered_after_a_fix():
    report = Report(silent_catches=Counter({"static/a.js": 1}))
    problems = compare(report, {"try_blocks": {}, "silent_catches": {"static/a.js": 2}})
    assert problems == ["silent_catches shrank in static/a.js: 2 -> 1; lower the baseline"]


def test_compare_is_clean_when_counts_match_baseline():
    report = Report(try_blocks=Counter({"static/a.js": 3}))
    assert compare(report, {"try_blocks": {"static/a.js": 3}, "silent_catches": {}}) == []


def test_repository_matches_committed_baseline():
    problems = compare(scan_repository(), load_baseline())
    assert problems == [], "run scripts/check_exception_handling.py to see details"


def test_vendored_eruda_and_test_files_are_not_scanned():
    report = scan_repository()
    scanned = set(report.try_blocks) | set(report.silent_catches)
    assert "static/eruda.js" not in scanned
    assert not any(path.startswith(("tests/", "node_modules/")) for path in scanned)


def test_main_fails_closed_on_regression(tmp_path, monkeypatch, capsys):
    import scripts.check_exception_handling as gate

    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"try_blocks": {}, "silent_catches": {}}', encoding="utf-8")
    monkeypatch.setattr(gate, "BASELINE", baseline)
    monkeypatch.setattr(
        gate, "scan_repository", lambda: Report(silent_catches=Counter({"x.js": 1}))
    )
    assert main([]) == 1
    assert "silent_catches grew in x.js" in capsys.readouterr().err


def test_silent_arrow_promise_catch_is_counted():
    assert scan_source("p.catch(() => {});").silent_catches == 1
    assert scan_source("p.catch(e => {});").silent_catches == 1


def test_arrow_promise_catch_that_reports_is_not_silent():
    assert scan_source("p.catch(e => { log(e); });").silent_catches == 0


def test_java_try_with_resources_is_counted():
    source = "try (InputStream in = open()) { read(in); }"
    assert scan_source(source).try_blocks == 1


def test_directories_named_tests_are_scanned_below_top_level(tmp_path):
    from scripts.check_exception_handling import _project_files

    nested = tmp_path / "static" / "tests"
    nested.mkdir(parents=True)
    (nested / "ui.js").write_text("try { a(); } catch (e) {}", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "skip.js").write_text("try { a(); }", encoding="utf-8")
    assert _project_files(tmp_path) == [nested / "ui.js"]
