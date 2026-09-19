from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "templates" / "index.html"
CSS = ROOT / "static" / "style.css"
ERUDA = ROOT / "static" / "vendor" / "eruda.min.js"
FAVICON = ROOT / "static" / "favicon.svg"

EXTERNAL_TAG_ASSET = re.compile(
    r"<(?:script|link|img|source|video|audio)[^>]+(?:src|href)\s*=\s*['\"]\s*(?:https?:)?//",
    re.IGNORECASE,
)


def test_html_assets_are_local():
    html = INDEX.read_text(encoding="utf-8")
    assert not EXTERNAL_TAG_ASSET.search(html)
    assert '/static/favicon.svg' in html
    assert '/static/vendor/eruda.min.js' in html


def test_local_debug_assets_exist():
    assert FAVICON.is_file() and FAVICON.stat().st_size > 0
    assert ERUDA.is_file() and ERUDA.stat().st_size > 100_000
    assert "eruda v3.4.3" in ERUDA.read_text(encoding="utf-8", errors="ignore")[:200].lower()


def test_css_does_not_load_remote_assets():
    css = CSS.read_text(encoding="utf-8")
    assert not re.search(r"@import\s+(?:url\()?\s*['\"]?(?:https?:)?//", css, re.IGNORECASE)
    assert not re.search(r"url\(\s*['\"]?(?:https?:)?//", css, re.IGNORECASE)
