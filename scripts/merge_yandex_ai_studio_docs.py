#!/usr/bin/env python3
"""Merge the Yandex AI Studio documentation into one Markdown file.

Reads a checkout of yandex-cloud/docs (``ru/ai-studio`` plus the shared ``ru/_includes``,
``ru/_roles``, ``ru/_pricing*``, ``ru/_untranslatable`` and ``ru/presets.yaml``) and
writes every page in the order of ``ru/ai-studio/toc.yaml``, then the pages the table
of contents does not list. ``{% include %}`` directives are resolved recursively and
``{{ variables }}`` are replaced from presets.yaml; page headings move one level down
under a per-page heading. The text is otherwise unchanged (CC BY 4.0, YANDEX LLC).

Usage: python scripts/merge_yandex_ai_studio_docs.py DOCS_CHECKOUT OUTPUT.md
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

INCLUDE = re.compile(r"\{%\s*include\s+(?:notitle\s+)?\[[^\]]*\]\(([^)\s]+)\)\s*%\}")
VARIABLE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})(\s)")


def front_matter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return text[end + 4 :].lstrip("\n")
    return text


def resolve(path: Path, root: Path, depth: int = 0) -> str:
    if depth > 8 or not path.is_file():
        return f"<!-- include not found: {path.relative_to(root) if path.is_relative_to(root) else path} -->"
    text = front_matter(path.read_text(encoding="utf-8", errors="replace"))

    def include(match: re.Match[str]) -> str:
        target = (path.parent / match.group(1).split("#", 1)[0]).resolve()
        return resolve(target, root, depth + 1).strip("\n")

    return INCLUDE.sub(include, text)


def substitute(text: str, presets: dict[str, str]) -> str:
    for _ in range(4):
        replaced = VARIABLE.sub(lambda m: str(presets.get(m.group(1), m.group(0))), text)
        if replaced == text:
            break
        text = replaced
    return text


def demote(text: str) -> str:
    out, fenced = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            line = HEADING.sub(lambda m: "#" + m.group(1) + m.group(2), line)
        out.append(line)
    return "\n".join(out)


def toc_pages(items: list, base: Path, trail: list[str], acc: list) -> None:
    for item in items or []:
        name = str(item.get("name") or "")
        href = item.get("href")
        if isinstance(href, str) and href.endswith(".md") and "://" not in href:
            acc.append(((base / href).resolve(), " › ".join([*trail, name])))
        if item.get("items"):
            toc_pages(item["items"], base, [*trail, name], acc)
        if isinstance(item.get("include"), dict):
            sub = item["include"].get("path")
            if isinstance(sub, str):
                sub_path = (base / sub).resolve()
                if sub_path.is_file():
                    data = yaml.safe_load(sub_path.read_text(encoding="utf-8")) or {}
                    toc_pages(data.get("items"), sub_path.parent, [*trail, name], acc)


def anchor(index: int) -> str:
    return f"page-{index:03d}"


def main() -> int:
    checkout, output = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    ru = checkout / "ru"
    studio = ru / "ai-studio"
    presets = (yaml.safe_load((ru / "presets.yaml").read_text(encoding="utf-8")) or {}).get(
        "default", {}
    )
    toc = yaml.safe_load((studio / "toc.yaml").read_text(encoding="utf-8")) or {}
    ordered: list = []
    toc_pages(toc.get("items"), studio, [], ordered)
    seen, pages = set(), []
    for path, title in ordered:
        if path not in seen and path.is_file() and path.is_relative_to(studio):
            seen.add(path)
            pages.append((path, title))
    for path in sorted(studio.rglob("*.md")):
        if path.resolve() not in seen:
            seen.add(path.resolve())
            pages.append((path.resolve(), "Вне оглавления"))

    revision = subprocess.run(
        ["git", "-C", str(checkout), "log", "-1", "--format=%H %cs"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    head = [
        "# Документация Yandex AI Studio — одним файлом",
        "",
        (
            "Все страницы `ru/ai-studio` из [yandex-cloud/docs]"
            f"(https://github.com/yandex-cloud/docs/tree/{revision.split()[0] if revision else 'master'}/ru/ai-studio)"
            f", ревизия `{revision}` — тот же снимок, что и у `contracts/yandex/catalog.json`."
        ),
        "Порядок — по `toc.yaml`, затем страницы вне оглавления. Вставки `{% include %}` раскрыты,",
        "переменные `{{ ... }}` подставлены из `ru/presets.yaml`, заголовки страниц сдвинуты на уровень.",
        "Иначе текст не менялся. Позже этого снимка в живой документации появились поля, которых",
        "здесь нет (например, `defer_loading` у function-инструментов, см. `api/yandex_request_profiles.py`).",
        "",
        (
            "© YANDEX LLC, 2018 и участники. Лицензия [CC BY 4.0]"
            "(https://github.com/yandex-cloud/docs/blob/master/LICENSE). Собрано скриптом"
        ),
        "`scripts/merge_yandex_ai_studio_docs.py`.",
        "",
        f"Страниц: {len(pages)}.",
        "",
        "## Содержание",
        "",
    ]
    body = []
    for index, (path, title) in enumerate(pages, 1):
        rel = path.relative_to(studio).as_posix()
        title = substitute(title, presets)
        head.append(f"{index}. [{title or rel}](#{anchor(index)}) — `{rel}`")
        text = demote(substitute(resolve(path, ru), presets)).strip()
        body.append(f'\n\n<a id="{anchor(index)}"></a>\n\n# {index}. {title or rel}\n\n`ru/ai-studio/{rel}`\n\n{text}\n')
    output.write_text("\n".join(head) + "\n" + "".join(body), encoding="utf-8")
    print(f"{len(pages)} pages -> {output} ({output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
