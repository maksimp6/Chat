from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFERRED_MODULES = (
    "models.js",
    "sidebar.js",
    "settings.js",
    "voice.js",
)

def test_deferred_modules_do_not_wait_for_domcontentloaded():
    for filename in DEFERRED_MODULES:
        script = (ROOT / "static" / filename).read_text(encoding="utf-8")
        assert "DOMContentLoaded" not in script, (
            f"{filename} is loaded with defer and must not add a redundant DOMContentLoaded gate"
        )

def test_critical_boot_script_remains_synchronous():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    marker = '<script id="alice-boot"'
    start = html.index(marker)
    tag_end = html.index(">", start) + 1
    tag = html[start:tag_end]
    assert " defer" not in tag
    assert " async" not in tag
