import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_RE = re.compile(r'<script[^>]+src="{{ static_root }}/([^"]+)"[^>]*>')


def test_index_references_existing_versioned_local_scripts():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    scripts = SCRIPT_RE.findall(html)
    assert scripts, "index.html should load local application scripts"

    for relative in scripts:
        path = ROOT / "static" / relative.split("?")[0]
        assert path.is_file(), f"Missing local script: {relative}"
        assert "?v={{ static_version }}" in relative, (
            f"Local script must be versioned: {relative}"
        )


def test_critical_boot_script_is_local_and_synchronous():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert '<script id="alice-boot" src="{{ static_root }}/boot.js?v={{ static_version }}"' in html
    assert ' defer' not in html.split('<script id="alice-boot"', 1)[1].split('</script>', 1)[0]


def test_local_script_tags_are_deferred_except_critical_boot():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    for tag in re.findall(r'<script[^>]+src="{{ static_root }}/[^"]+"[^>]*>', html):
        if '/boot.js?' in tag:
            continue
        assert " defer" in tag, f"Application script is not deferred: {tag}"


def test_index_has_no_inline_application_script():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert not re.search(r'<script(?![^>]+src=)[^>]*>.*?</script>', html, re.DOTALL)
