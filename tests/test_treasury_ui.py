from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_treasury_uses_one_header_entry_point():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    header = (ROOT / "static" / "header_actions.js").read_text(encoding="utf-8")

    assert html.count('id="treasury-btn"') == 1
    assert 'data-action="header.treasury.open"' in html
    assert 'id="expenses-btn"' not in html
    assert 'id="top-up-btn"' not in html
    assert 'actions.register("header.treasury.open"' in header
    assert 'call("openTreasuryPanel")' in header


def test_treasury_modal_contains_both_actions_and_balance():
    js = (ROOT / "static" / "treasury.js").read_text(encoding="utf-8")

    assert "treasury-modal" in js
    assert "/api/treasury/account" in js
    assert "/api/treasury/top-up" in js
    assert "treasuryExpenses" in js
    assert "treasuryBalance" in js
    assert "openExpensesPanel" not in js
    assert "prompt(" not in js


def test_treasury_modal_is_mobile_safe():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert ".treasury-modal" in css
    assert "safe-area-inset-top" in css
    assert "safe-area-inset-bottom" in css
    assert "100dvh" in css
