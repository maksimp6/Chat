"""Локальные MCP-инструменты для поиска и чтения статей в Wikipedia."""

import requests
import logging

logger = logging.getLogger("wikipedia_mcp")


def wikipedia_search(arguments: dict, cfg: dict = None) -> dict:
    query = arguments.get("query")
    lang = arguments.get("lang", "ru")
    if not query:
        return {"success": False, "error": "Не указан поисковый запрос (query)"}

    url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {"action": "query", "list": "search", "srsearch": query, "format": "json"}
    try:
        headers = {"User-Agent": "AlicePro/1.0"}
        res = requests.get(url, params=params, headers=headers, timeout=10)
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
    lang = arguments.get("lang", "ru")
    if not title:
        return {"success": False, "error": "Не указан заголовок статьи (title)"}

    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(title)}"
    try:
        headers = {"User-Agent": "AlicePro/1.0"}
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            return {"success": False, "error": f"Статья не найдена (код {res.status_code})"}
        data = res.json()
        return {
            "success": True,
            "title": data.get("title"),
            "extract": data.get("extract"),
            "url": data.get("content_urls", {}).get("desktop", {}).get("page", ""),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


TOOL_REGISTRY = {
    "wikipedia_search": {
        "func": wikipedia_search,
        "description": "Поиск статей в Wikipedia по ключевым словам.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос"},
                "lang": {"type": "string", "description": "Язык ('ru', 'en'), по умолчанию 'ru'"},
            },
            "required": ["query"],
        },
    },
    "wikipedia_summary": {
        "func": wikipedia_summary,
        "description": "Получить краткое содержание (summary) статьи из Wikipedia по ее точному заголовку.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Точное название статьи"},
                "lang": {"type": "string", "description": "Язык ('ru', 'en'), по умолчанию 'ru'"},
            },
            "required": ["title"],
        },
    },
}

WIKIPEDIA_TOOLS = TOOL_REGISTRY
