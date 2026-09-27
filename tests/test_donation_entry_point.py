import re

import pytest

import app as app_module
import config


VALID_URL = "https://yoomoney.ru/fundraise/example-project"


def _render_index(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ALICE_DONATION_URL", raising=False)
    else:
        monkeypatch.setenv("ALICE_DONATION_URL", value)
    response = app_module.app.test_client().get("/")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_donation_url_disabled_when_unset(monkeypatch):
    monkeypatch.delenv("ALICE_DONATION_URL", raising=False)
    assert config.get_donation_url() is None


@pytest.mark.parametrize(
    "value",
    [
        VALID_URL,
        "http://example.org/donate?utm=alice",
        f"  {VALID_URL}  ",
    ],
)
def test_donation_url_accepts_absolute_http_urls(monkeypatch, value):
    monkeypatch.setenv("ALICE_DONATION_URL", value)
    assert config.get_donation_url() == value.strip()


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://example.org/donate",
        "//example.org/donate",
        "/donate",
        "https://",
        "https://user:secret@example.org/donate",
        "https://token@example.org/donate",
        "https://example.org/do nate",
        "https://example.org/\ndonate",
        "https://[::1/donate",
    ],
)
def test_donation_url_rejects_unsafe_values(monkeypatch, value):
    monkeypatch.setenv("ALICE_DONATION_URL", value)
    assert config.get_donation_url() is None


def test_rejected_donation_url_is_not_logged(monkeypatch, caplog):
    secret_url = "https://user:very-secret@example.org/donate"
    monkeypatch.setenv("ALICE_DONATION_URL", secret_url)
    with caplog.at_level("WARNING"):
        assert config.get_donation_url() is None
    assert "very-secret" not in caplog.text
    assert "ALICE_DONATION_URL ignored" in caplog.text


def test_index_hides_donation_button_when_not_configured(monkeypatch):
    html = _render_index(monkeypatch, None)
    assert 'id="donate-btn"' not in html
    assert "icons/donate.svg" not in html


def test_index_hides_donation_button_for_invalid_url(monkeypatch):
    html = _render_index(monkeypatch, "javascript:alert(1)")
    assert 'id="donate-btn"' not in html
    assert "javascript:" not in html


def test_index_renders_external_donation_link_when_configured(monkeypatch):
    html = _render_index(monkeypatch, VALID_URL)
    match = re.search(r'<a[^>]*id="donate-btn"[^>]*>', html)
    assert match, "donation button must be rendered when configured"
    tag = match.group(0)
    assert f'href="{VALID_URL}"' in tag
    assert 'target="_blank"' in tag
    assert 'rel="noopener noreferrer external"' in tag
    assert "onclick=" not in tag
    assert 'aria-label="Поддержать проект"' in tag


def test_donation_url_query_is_html_escaped(monkeypatch):
    html = _render_index(monkeypatch, 'https://example.org/donate?a=1&b="x"')
    assert 'href="https://example.org/donate?a=1&amp;b=&#34;x&#34;"' in html


def test_donation_icon_is_repository_local(monkeypatch):
    html = _render_index(monkeypatch, VALID_URL)
    icon = re.search(r'<a[^>]*id="donate-btn"[^>]*>(.*?)</a\s*>', html, re.S).group(1)
    assert re.search(r'src="/static/icons/donate\.svg\?v=[^"]+"', icon)
    svg = (
        app_module.app.static_folder
        and open(f"{app_module.app.static_folder}/icons/donate.svg", encoding="utf-8").read()
    )
    assert "<svg" in svg
    assert "http://" not in svg.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in svg
