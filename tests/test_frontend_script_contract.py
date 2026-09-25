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


def test_local_script_tags_are_deferred():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    for tag in re.findall(r'<script[^>]+src="{{ static_root }}/[^"]+"[^>]*>', html):
        assert " defer" in tag, f"Application script is not deferred: {tag}"
