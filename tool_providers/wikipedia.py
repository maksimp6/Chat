"""Локальные MCP-инструменты для поиска и чтения статей в Wikipedia."""

import logging
import re
import time
from typing import Any

import requests

logger = logging.getLogger("logos.tool_providers.wikipedia")

# Wikimedia rate-limits generic agents; its policy asks for a descriptive User-Agent.
USER_AGENT = (
    "Logos/1.0 (https://github.com/maksimp6/Chat; assistant tools) "
    + requests.utils.default_user_agent()
)
# Language code is part of the host name: only Wikipedia-style codes (ru, en, zh-yue).
_LANG = re.compile(r"[a-z]{2,12}(?:-[a-z0-9]{1,12}){0,2}")
_RETRY_STATUSES = {429, 503}
_MAX_RETRY_AFTER = 5.0


def _lang(arguments: dict[str, Any]) -> str:
    lang = str(arguments.get("lang") or "ru").strip().lower()
    if not _LANG.fullmatch(lang):
        raise ValueError(f"Недопустимый код языка Wikipedia: {lang!r}")
    return lang


def _get(url: str, **kwargs: Any) -> requests.Response:
    """GET with one bounded retry when Wikimedia asks to slow down."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    res = requests.get(url, headers=headers, timeout=10, **kwargs)
    if res.status_code in _RETRY_STATUSES:
        try:
            delay = float(res.headers.get("Retry-After", 1))
        except ValueError:
            delay = 1.0
        time.sleep(min(max(delay, 0.0), _MAX_RETRY_AFTER))
        res = requests.get(url, headers=headers, timeout=10, **kwargs)
    return res


def _http_error(res: requests.Response) -> dict[str, Any]:
    if res.status_code == 429:
        return {
            "success": False,
            "error": "Wikipedia ограничила частоту запросов (HTTP 429); повторите позже",
            "status": 429,
        }
    return {
        "success": False,
        "error": f"Wikipedia ответила HTTP {res.status_code}",
        "status": res.status_code,
    }


def wikipedia_search(arguments: dict, cfg: dict = None) -> dict:
    query = arguments.get("query")
    if not query:
        return {"success": False, "error": "Не указан поисковый запрос (query)"}

    lang = _lang(arguments)
    url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {"action": "query", "list": "search", "srsearch": query, "format": "json"}
    try:
        res = _get(url, params=params)
        if res.status_code != 200:
            return _http_error(res)
        data = res.json()
        search_results = data.get("query", {}).get("search", [])
        results = [
            {
                "title": item["title"],
                "snippet": item["snippet"]
                .replace('<span class="searchmatch">', "")
                .replace("</span>", ""),
            }
            for item in search_results[:5]
        ]
        return {"success": True, "results": results}
    except Exception as e:
        return {"success": False, "error": str(e)}


def wikipedia_summary(arguments: dict, cfg: dict = None) -> dict:
    title = arguments.get("title")
    if not title:
        return {"success": False, "error": "Не указан заголовок статьи (title)"}

    lang = _lang(arguments)
    page = requests.utils.quote(str(title).strip().replace(" ", "_"), safe="")
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{page}"
    try:
        res = _get(url)
        if res.status_code == 404:
            return {
                "success": False,
                "error": "Статья не найдена (код 404)",
                "status": 404,
            }
        if res.status_code != 200:
            return _http_error(res)
        data = res.json()
        return {
            "success": True,
            "title": data.get("title"),
            "extract": data.get("extract"),
            "url": data.get("content_urls", {}).get("desktop", {}).get("page", ""),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


_READ_ONLY = {"read_only": True, "requires_approval": False, "risk_level": "low"}

TOOL_REGISTRY = {
    "wikipedia_search": {
        "func": wikipedia_search,
        "description": "Поиск статей в Wikipedia по ключевым словам.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос"},
                "lang": {
                    "type": "string",
                    "description": "Язык ('ru', 'en'), по умолчанию 'ru'",
                },
            },
            "required": ["query"],
        },
        **_READ_ONLY,
    },
    "wikipedia_summary": {
        "func": wikipedia_summary,
        "description": "Получить краткое содержание (summary) статьи из Wikipedia по ее точному заголовку.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Точное название статьи"},
                "lang": {
                    "type": "string",
                    "description": "Язык ('ru', 'en'), по умолчанию 'ru'",
                },
            },
            "required": ["title"],
        },
        **_READ_ONLY,
    },
}

WIKIPEDIA_TOOLS = TOOL_REGISTRY
