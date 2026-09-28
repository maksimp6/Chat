import ast
import json
from pathlib import Path

import pytest

from docs_mirror import crawler
from docs_mirror import FetchResult, Mirror, html_to_markdown, normalize_url, url_to_path


def page(title, body, links=()):
    anchors = "".join(f'<a href="{href}">{href}</a>' for href in links)
    return (
        f"<html><head><title>{title}</title><script>track()</script></head><body>"
        f"<nav><a href='/docs/nav-only'>menu</a></nav>"
        f"<main><h1>{title}</h1><p>{body}</p>{anchors}</main>"
        f"<footer>© Cloud.ru</footer></body></html>"
    )


class FakeSite:
    def __init__(self, pages):
        self.pages = dict(pages)
        self.requests = []

    def __call__(self, url):
        self.requests.append(url)
        if url not in self.pages:
            return FetchResult(status=404)
        return FetchResult(status=200, text=self.pages[url])


BASE = "https://cloud.ru/docs/svc/ug"


def make_mirror(tmp_path, site):
    return Mirror(tmp_path, host="cloud.ru", prefixes=["/docs/svc"], fetcher=site, delay_s=0)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://cloud.ru/docs/svc/ug/index.html", "https://cloud.ru/docs/svc/ug/index"),
        ("http://WWW.Cloud.ru/ru/docs/svc/ug/a.html?utm=1#top", "https://cloud.ru/docs/svc/ug/a"),
        ("https://cloud.ru/docs/svc/ug/a/", "https://cloud.ru/docs/svc/ug/a"),
        ("https://cloud.ru//docs//svc", "https://cloud.ru/docs/svc"),
        ("mailto:x@cloud.ru", None),
        ("javascript:void(0)", None),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_relative_links_resolve_against_page():
    assert normalize_url("../topics/b.html", base=f"{BASE}/topics/a") == f"{BASE}/topics/b"


def test_url_to_path_is_safe():
    assert url_to_path(f"{BASE}/topics/a") == "svc/ug/topics/a.md"
    assert url_to_path("https://cloud.ru/docs") == "index.md"
    assert ".." not in url_to_path("https://cloud.ru/docs/%2e%2e/x")
    spaced = url_to_path("https://cloud.ru/docs/a b/c?")
    assert spaced.startswith("a_b-") and spaced.endswith("/c.md")


def test_url_to_path_has_no_collisions():
    paths = {
        url_to_path("https://cloud.ru/docs/svc/a%20b"),
        url_to_path("https://cloud.ru/docs/svc/a_b"),
        url_to_path("https://cloud.ru/docs/svc/a b"),
    }
    assert len(paths) == 3
    assert "svc/a_b.md" in paths


def test_html_to_markdown_keeps_content_and_drops_chrome():
    html = (
        "<html><head><title>T</title><style>x{}</style></head><body><nav>menu</nav><main>"
        "<h2>Setup</h2><p>Run <code>docker login</code> with <strong>key</strong>.</p>"
        "<ul><li>one</li><li>two</li></ul><ol><li>first</li></ol>"
        "<pre>curl -X POST \\\n  https://x</pre>"
        "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2|3</td></tr></table>"
        '<a href="/docs/svc/ug/b.html">B page</a>'
        "</main><footer>foot</footer></body></html>"
    )
    title, md, links = html_to_markdown(html, f"{BASE}/a")
    assert title == "T"
    assert "## Setup" in md
    assert "Run `docker login` with **key**." in md
    assert "- one" in md and "- two" in md and "1. first" in md
    assert "```\ncurl -X POST \\\n  https://x\n```" in md
    assert "| A | B |" in md and "| 1 | 2\\|3 |" in md
    assert "[B page](https://cloud.ru/docs/svc/ug/b)" in md
    assert "menu" not in md and "foot" not in md and "x{}" not in md
    assert "/docs/svc/ug/b.html" in links


def test_crawl_follows_scope_dedupes_and_writes_manifest(tmp_path):
    site = FakeSite(
        {
            f"{BASE}/index": page(
                "Index", "hello", [f"{BASE}/a.html", "/ru/docs/svc/ug/a", "/docs/other/x"]
            ),
            f"{BASE}/a": page("A", "alpha", [f"{BASE}/index"]),
        }
    )
    stats = make_mirror(tmp_path, site).crawl([f"{BASE}/index"])

    assert stats.as_dict() == {
        "created": 2,
        "updated": 0,
        "unchanged": 0,
        "errors": 0,
        "missing": 0,
    }
    assert site.requests == [f"{BASE}/index", f"{BASE}/a"]  # /docs/other and nav link out of scope
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    entry = next(p for p in manifest["pages"] if p["canonical_url"] == f"{BASE}/a")
    assert entry["path"] == "svc/ug/a.md"
    assert entry["status"] == "ok" and len(entry["sha256"]) == 64
    content = (tmp_path / "svc/ug/a.md").read_text()
    assert content.startswith(f"<!-- source: {BASE}/a -->")
    assert "alpha" in content


def test_incremental_refresh_detects_unchanged_changed_and_missing(tmp_path):
    site = FakeSite(
        {
            f"{BASE}/index": page("Index", "hello", [f"{BASE}/a", f"{BASE}/b"]),
            f"{BASE}/a": page("A", "alpha"),
            f"{BASE}/b": page("B", "beta"),
        }
    )
    make_mirror(tmp_path, site).crawl([f"{BASE}/index"])
    first = json.loads((tmp_path / "manifest.json").read_text())
    a_sha = next(p["sha256"] for p in first["pages"] if p["path"] == "svc/ug/a.md")
    mtime = (tmp_path / "svc/ug/index.md").stat().st_mtime_ns

    site.pages[f"{BASE}/a"] = page("A", "alpha v2")
    del site.pages[f"{BASE}/b"]  # still linked, now 404
    stats = make_mirror(tmp_path, site).crawl([f"{BASE}/index"])

    assert stats.unchanged == 1 and stats.updated == 1 and stats.errors == 1
    assert (tmp_path / "svc/ug/index.md").stat().st_mtime_ns == mtime
    second = {p["path"]: p for p in json.loads((tmp_path / "manifest.json").read_text())["pages"]}
    assert second["svc/ug/a.md"]["previous_sha256"] == a_sha
    assert second["svc/ug/b.md"]["status"] == "missing"
    assert (tmp_path / "svc/ug/b.md").exists()  # never deleted automatically


def test_pages_no_longer_linked_are_marked_missing(tmp_path):
    site = FakeSite(
        {
            f"{BASE}/index": page("Index", "hello", [f"{BASE}/b"]),
            f"{BASE}/b": page("B", "beta"),
        }
    )
    make_mirror(tmp_path, site).crawl([f"{BASE}/index"])
    site.pages[f"{BASE}/index"] = page("Index", "hello")
    stats = make_mirror(tmp_path, site).crawl([f"{BASE}/index"])

    assert stats.missing == 1
    manifest = {p["path"]: p for p in json.loads((tmp_path / "manifest.json").read_text())["pages"]}
    assert manifest["svc/ug/b.md"]["status"] == "missing"


def test_failed_fetch_is_recorded_and_crawl_continues(tmp_path):
    def fetcher(url):
        if url.endswith("/a"):
            raise ConnectionError("down")
        return FetchResult(status=200, text=page("Index", "hi", [f"{BASE}/a", f"{BASE}/c"]))

    stats = make_mirror(tmp_path, fetcher).crawl([f"{BASE}/index"])
    manifest = {p["path"]: p for p in json.loads((tmp_path / "manifest.json").read_text())["pages"]}
    assert stats.errors == 1
    assert manifest["svc/ug/a.md"]["status"] == "error"
    assert manifest["svc/ug/a.md"]["error"] == "ConnectionError"
    assert manifest["svc/ug/c.md"]["status"] == "ok"


def test_crawler_path_has_no_llm_dependency():
    tree = ast.parse(Path(crawler.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {
        "anthropic",
        "openai",
        "yandex_client",
        "agent_gateway",
        "cloudru_api_key_provider",
    }
    assert not imported & forbidden


def test_inline_markup_images_and_rules():
    html = (
        "<main><p>a<br>b <em>soft</em> <i>it</i></p><hr>"
        '<img src="/img/x.png" alt=" Diagram "><img alt="no src"></main>'
    )
    _, md, _ = html_to_markdown(html, f"{BASE}/a")
    assert "a\nb *soft* *it*" in md
    assert "---" in md
    assert "![Diagram](https://cloud.ru/img/x.png)" in md
    assert "no src" not in md


def test_render_page_adds_title_only_when_missing():
    assert "\n# T\n" in docs_mirror_render("T", "body\n")
    assert docs_mirror_render("T", "# Own\n").count("# ") == 1


def docs_mirror_render(title, markdown):
    return crawler.render_page(f"{BASE}/a", title, markdown)


def test_scope_rejects_other_hosts():
    assert not crawler.in_scope("https://example.com/docs/svc/a", "cloud.ru", ["/docs/svc"])
    assert crawler.in_scope("https://cloud.ru/docs/svc", "cloud.ru", ["/docs/svc"])


def test_unsafe_target_path_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        make_mirror(tmp_path, FakeSite({}))._target("../outside.md")


def test_crawl_sleeps_between_requests(tmp_path):
    sleeps = []
    site = FakeSite(
        {f"{BASE}/index": page("Index", "x", [f"{BASE}/a"]), f"{BASE}/a": page("A", "y")}
    )
    mirror = Mirror(
        tmp_path,
        host="cloud.ru",
        prefixes=["/docs/svc"],
        fetcher=site,
        delay_s=0.25,
        sleep=sleeps.append,
    )
    mirror.crawl([f"{BASE}/index"])
    assert sleeps == [0.25]


def test_requests_fetcher_maps_response(monkeypatch):
    import requests

    calls = []

    class FakeResponse:
        status_code = 200
        ok = True
        encoding = None
        text = "<p>hi</p>"
        headers = {"Content-Type": "text/html", "Last-Modified": "Mon"}
        url = f"{BASE}/a"

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, url, timeout):
            calls.append((url, timeout, dict(self.headers)))
            return FakeResponse()

    monkeypatch.setattr(requests, "Session", FakeSession)
    result = crawler.requests_fetcher(timeout=3)(f"{BASE}/a")
    assert result == FetchResult(200, "<p>hi</p>", "text/html", "Mon", f"{BASE}/a")
    assert calls[0][1] == 3 and "AliceProDocsMirror" in calls[0][2]["User-Agent"]


def test_unsupported_link_schemes_become_plain_text():
    html = '<main><a href="javascript:alert(1)">bad</a> <a href="/docs/svc/ug/b">ok</a></main>'
    _, md, _ = html_to_markdown(html, f"{BASE}/a")
    assert "javascript" not in md
    assert "bad [ok](https://cloud.ru/docs/svc/ug/b)" in md


def test_out_of_scope_seeds_are_ignored(tmp_path):
    site = FakeSite({"https://cloud.ru/docs/other/x": page("X", "x")})
    stats = make_mirror(tmp_path, site).crawl(
        ["https://cloud.ru/docs/other/x", "https://evil.test/docs/svc"]
    )
    assert site.requests == []
    assert stats.as_dict()["created"] == 0


def test_redirect_out_of_scope_is_not_stored(tmp_path):
    def fetcher(url):
        return FetchResult(status=200, text=page("Evil", "x"), final_url="https://evil.test/page")

    stats = make_mirror(tmp_path, fetcher).crawl([f"{BASE}/index"])
    manifest = {p["path"]: p for p in json.loads((tmp_path / "manifest.json").read_text())["pages"]}
    assert stats.errors == 1
    assert manifest["svc/ug/index.md"]["error"].startswith("redirected out of scope")
    assert not (tmp_path / "svc/ug/index.md").exists()


def test_failed_refresh_marks_error_and_skips_missing_sweep(tmp_path):
    site = FakeSite(
        {
            f"{BASE}/index": page("Index", "hello", [f"{BASE}/a"]),
            f"{BASE}/a": page("A", "alpha", [f"{BASE}/b"]),
            f"{BASE}/b": page("B", "beta"),
        }
    )
    make_mirror(tmp_path, site).crawl([f"{BASE}/index"])

    def flaky(url):
        if url.endswith("/a"):
            return FetchResult(status=503)
        return site(url)

    stats = make_mirror(tmp_path, flaky).crawl([f"{BASE}/index"])
    manifest = {p["path"]: p for p in json.loads((tmp_path / "manifest.json").read_text())["pages"]}
    assert stats.errors == 1 and stats.missing == 0
    assert manifest["svc/ug/a.md"]["status"] == "error"
    assert manifest["svc/ug/b.md"]["status"] == "ok"


def test_unsafe_image_schemes_are_dropped():
    _, md, _ = html_to_markdown(
        '<main><img src="javascript:alert(1)" alt="js">'
        '<img src="data:image/png;base64,AAAA" alt="data">'
        '<img src="https://cloud.ru/ok.png" alt="ok"></main>',
        page_url=f"{BASE}/topics/a",
    )
    assert "javascript:" not in md
    assert "data:" not in md
    assert "![ok](https://cloud.ru/ok.png)" in md
