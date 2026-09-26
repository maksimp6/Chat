"""Live Flask-port resource contract tests.

The CI job starts the real Flask app and points ALICE_LIVE_BASE_URL at it.
"""

import os
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


BASE_URL = os.environ.get("ALICE_LIVE_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
TIMEOUT = 10


class ResourceParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.resources = []
        self.html_charset = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and "charset" in attrs:
            self.html_charset.append(attrs["charset"])
        if tag == "script" and attrs.get("src"):
            self.resources.append(("script", attrs["src"]))
        if tag == "link" and attrs.get("href"):
            self.resources.append(("link", attrs["href"]))


def fetch(path="/"):
    url = urljoin(BASE_URL + "/", path.lstrip("/"))
    request = Request(url, headers={"User-Agent": "Alice-Pro-CI-Resource-Test/1.0"})
    with urlopen(request, timeout=TIMEOUT) as response:
        return response.status, response.headers, response.read(), response.geturl()


def wait_for_app():
    last_error = None
    for _ in range(30):
        try:
            return fetch("/")
        except Exception as exc:
            last_error = exc
            import time

            time.sleep(1)
    raise AssertionError(f"Flask app did not become ready: {last_error}")


def test_live_flask_serves_utf8_html():
    status, headers, body, final_url = wait_for_app()
    assert status == 200
    assert urlparse(final_url).scheme in {"http", "https"}
    assert "text/html" in headers.get_content_type()
    assert body.decode("utf-8").encode("utf-8") == body
    assert not body.startswith(b"\xef\xbb\xbf")

    parser = ResourceParser()
    parser.feed(body.decode("utf-8"))
    assert parser.html_charset
    assert all(value.lower() == "utf-8" for value in parser.html_charset)


def test_live_flask_resources_are_local_and_loadable():
    _, _, body, _ = fetch("/")
    html = body.decode("utf-8")
    parser = ResourceParser()
    parser.feed(html)

    assert parser.resources, "live page contains no browser resources"

    for tag, raw_ref in parser.resources:
        assert "{{" not in raw_ref and "}}" not in raw_ref, (
            f"unrendered template resource: {raw_ref}"
        )
        parsed = urlparse(raw_ref)
        assert not parsed.scheme, f"external resource scheme: {raw_ref}"
        assert not parsed.netloc, f"external resource host: {raw_ref}"
        assert not raw_ref.startswith("//"), f"protocol-relative resource: {raw_ref}"

        resource_url = urljoin(BASE_URL + "/", raw_ref.lstrip("/"))
        resource_status, resource_headers, resource_body, _ = fetch(
            urlparse(resource_url).path
            + (("?" + urlparse(resource_url).query) if urlparse(resource_url).query else "")
        )
        assert resource_status == 200, f"{tag} resource failed: {raw_ref} -> HTTP {resource_status}"
        assert resource_body, f"{tag} resource is empty: {raw_ref}"

        content_type = resource_headers.get_content_type()
        if tag == "script":
            assert raw_ref.split("?", 1)[0].lower().endswith(".js"), raw_ref
            assert content_type in {"text/javascript", "application/javascript"}, (
                f"unexpected JS content type for {raw_ref}: {content_type}"
            )
            resource_body.decode("utf-8")
        elif raw_ref.split("?", 1)[0].lower().endswith(".css"):
            assert content_type == "text/css", (
                f"unexpected CSS content type for {raw_ref}: {content_type}"
            )
            resource_body.decode("utf-8")


def test_live_flask_static_paths_do_not_escape_static_root():
    _, _, body, _ = fetch("/")
    html = body.decode("utf-8")
    parser = ResourceParser()
    parser.feed(html)

    for _, raw_ref in parser.resources:
        clean = raw_ref.split("?", 1)[0]
        assert clean.startswith("/static/"), f"resource outside /static/: {raw_ref}"
        assert ".." not in clean.split("/"), f"path traversal in resource: {raw_ref}"
