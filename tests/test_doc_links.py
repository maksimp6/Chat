from __future__ import annotations

from pathlib import Path

from scripts.check_doc_links import validate_markdown_links


def test_validates_relative_and_root_local_links(tmp_path):
    (tmp_path / "docs" / "guide").mkdir(parents=True)
    (tmp_path / "docs" / "target.md").write_text("# Target\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Root\n", encoding="utf-8")
    source = tmp_path / "docs" / "guide" / "page.md"
    source.write_text("[target](../target.md) [root](/README.md)\n", encoding="utf-8")
    assert validate_markdown_links(tmp_path, [source]) == []


def test_reports_missing_local_target_with_source(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "page.md"
    source.write_text("[missing](nope.md)\n", encoding="utf-8")
    errors = validate_markdown_links(tmp_path, [source])
    assert errors == ["docs/page.md: missing local link target: nope.md"]


def test_ignores_external_anchor_mailto_and_inline_code(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "page.md"
    source.write_text(
        "[web](https://example.com/x) [anchor](#part) [mail](mailto:a@example.com) `fake/path.md`\n",
        encoding="utf-8",
    )
    assert validate_markdown_links(tmp_path, [source]) == []


def test_rejects_repository_escape(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "page.md"
    source.write_text("[escape](../../outside.md)\n", encoding="utf-8")
    errors = validate_markdown_links(tmp_path, [source])
    assert errors == ["docs/page.md: local link escapes repository: ../../outside.md"]
