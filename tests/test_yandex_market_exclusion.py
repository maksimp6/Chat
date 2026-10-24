"""Regression checks for the Logos Pro marketplace exclusion policy."""

import ast
from pathlib import Path

import pytest

import yandex_client_modules.mcp_mixin as mcp_mixin


def _production_blocklist():
    source = (Path(__file__).resolve().parents[1] / "chat" / "mcp_routes.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    client = next(
        item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "LogosClient"
    )
    assert any(
        isinstance(base, ast.Name) and base.id == "YandexResponsesClient" for base in client.bases
    )
    assignment = next(
        item
        for item in client.body
        if isinstance(item, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "mandatory_blocked_web_domains"
            for target in item.targets
        )
    )
    return tuple(ast.literal_eval(assignment.value))


class CapturingLogosClient(mcp_mixin.YandexMcpMixin):
    mandatory_blocked_web_domains = _production_blocklist()

    def ask(self, message, model_key, conversation_id=None, params=None, execution_trace=None):
        self.last_params = dict(params or {})
        return {"output": []}


def _capture_web_tools(monkeypatch, settings=None, params=None):
    monkeypatch.setattr(mcp_mixin.mcp_storage, "list_servers", lambda: [])
    monkeypatch.setattr(mcp_mixin, "get_conv_settings", lambda conversation_id: settings)
    monkeypatch.setattr(mcp_mixin.registry, "get_tools_by_category", lambda category: [])

    client = CapturingLogosClient()
    client.ask_with_mcp(
        "Find PETG nozzles",
        "logosai-llm",
        conversation_id="marketplace-policy",
        params=params,
    )
    return [
        tool for tool in client.last_params.get("tools", []) if tool.get("type") == "web_search"
    ]


def _normalize(domains):
    return {domain.casefold().rstrip(".") for domain in domains}


def test_default_web_search_blocks_marketplace_without_blocking_yandex_services(monkeypatch):
    tools = _capture_web_tools(monkeypatch)

    assert len(tools) == 1
    assert tools[0]["search_context_size"] == "medium"
    assert "allowed_domains" not in tools[0]["filters"]
    assert _normalize(tools[0]["filters"]["blocked_domains"]) == set(
        CapturingLogosClient.mandatory_blocked_web_domains
    )
    assert "yandex.ru" not in _normalize(tools[0]["filters"]["blocked_domains"])


def test_saved_allowlist_cannot_reenable_yandex_market(monkeypatch):
    settings = {
        "tools_config": {
            "web_search": {
                "enabled": True,
                "context_size": "high",
                "allowed_domains": (
                    "cloud.yandex.ru, market.yandex.ru, "
                    "MARKET.YANDEX.COM, *.market.yandex.ru, shop.example"
                ),
                "blocked_domains": "ads.example, MARKET.YANDEX.RU",
            }
        }
    }
    tools = _capture_web_tools(monkeypatch, settings=settings)

    assert len(tools) == 1
    assert tools[0]["search_context_size"] == "high"
    assert tools[0]["filters"]["allowed_domains"] == ["cloud.yandex.ru", "shop.example"]
    blocked = tools[0]["filters"]["blocked_domains"]
    assert "ads.example" in blocked
    assert _normalize(blocked).issuperset(CapturingLogosClient.mandatory_blocked_web_domains)
    assert sum(domain.casefold() == "market.yandex.ru" for domain in blocked) == 1


def test_request_level_web_settings_cannot_reenable_marketplace(monkeypatch):
    tools = _capture_web_tools(
        monkeypatch,
        params={
            "tools_config": {
                "web_search": {
                    "enabled": True,
                    "allowed_domains": "market.yandex.kz, ozon.ru, wb.ru",
                }
            }
        },
    )

    assert tools[0]["filters"]["allowed_domains"] == ["ozon.ru", "wb.ru"]
    assert "market.yandex.kz" in _normalize(tools[0]["filters"]["blocked_domains"])


@pytest.mark.parametrize("scope", ["saved", "request"])
def test_disabled_web_search_remains_disabled(monkeypatch, scope):
    config = {"tools_config": {"web_search": {"enabled": False}}}
    tools = _capture_web_tools(
        monkeypatch,
        settings=config if scope == "saved" else None,
        params=config if scope == "request" else None,
    )

    assert tools == []
