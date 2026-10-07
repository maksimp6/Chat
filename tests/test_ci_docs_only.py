from __future__ import annotations

from pathlib import Path

from scripts.ci_docs_only import is_docs_only

ROOT = Path(__file__).resolve().parents[1]


def test_docs_only_scope_is_narrow_and_fail_closed():
    assert is_docs_only(
        [
            "README.md",
            "docs/architecture/overview.md",
            "docs/catalog.json",
            ".github/ISSUE_TEMPLATE/documentation.md",
        ]
    )
    assert not is_docs_only([])
    assert not is_docs_only(["scripts/check_docs.py"])
    assert not is_docs_only(["docs/README.md", ".github/workflows/ci.yml"])
    assert not is_docs_only([".agents/skills/docs-sync/SKILL.md"])
    assert not is_docs_only(["AGENTS.md"])


def test_required_checks_keep_their_names_while_docs_only_takes_fast_path():
    security = (ROOT / ".github/workflows/security.yml").read_text(encoding="utf-8")
    codeql = (ROOT / ".github/workflows/codeql.yml").read_text(encoding="utf-8")
    formatting = (ROOT / ".github/workflows/format.yml").read_text(encoding="utf-8")

    assert "name: Trivy repository scan" in security
    assert "name: Zizmor GitHub Actions audit" in security
    assert "name: CodeQL (${{ matrix.language }})" in codeql
    assert "name: Auto-format repository" in formatting

    for workflow in (security, codeql, formatting):
        assert "ci_docs_only.py --null" in workflow

    for workflow in (security, codeql):
        assert '"**/*.md"' not in workflow
        assert '".agents/**"' not in workflow

    assert "format_changed_docs.sh" in formatting


def test_local_launch_smoke_ignores_documentation_only_pull_requests():
    workflow = (ROOT / ".github/workflows/launch-smoke.yml").read_text(encoding="utf-8")
    assert "paths-ignore:" in workflow
    assert '"docs/**"' in workflow
    assert '"README.md"' in workflow
    assert '"**/*.md"' not in workflow


def test_docs_only_formatter_is_a_gate_not_only_an_autofix():
    formatting = (ROOT / ".github/workflows/format.yml").read_text(encoding="utf-8")
    assert 'bash scripts/format_changed_docs.sh check "$BASE_SHA" HEAD' in formatting
    assert "Check changed documentation formatting" in formatting
