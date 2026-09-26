from pathlib import Path


def test_optional_debug_scripts_do_not_block_dom_ready():
    html = Path("templates/index.html").read_text(encoding="utf-8")

    for script in ("android_diagnostics.js", "eruda_init.js"):
        marker = f'src="{{{{ static_root }}}}/{script}?v={{{{ static_version }}}}"'
        start = html.find(marker)
        assert start >= 0, f"{script} is not present in the main template"
        end = html.find("</script>", start)
        assert end >= 0, f"{script} script tag is incomplete"

        tag = html[start:end]
        assert "async" in tag, f"{script} must remain non-critical"
        assert 'data-optional-script="true"' in tag
        assert " defer" not in tag, f"{script} must not hold DOMContentLoaded"


def test_critical_shell_is_not_hidden_by_optional_modules():
    html = Path("templates/index.html").read_text(encoding="utf-8")

    assert 'id="app-root"' in html
    assert 'id="msg-input"' in html

    optional_scripts = ("android_diagnostics.js", "eruda_init.js")
    for script in optional_scripts:
        assert f'data-optional-script="true"' in html
