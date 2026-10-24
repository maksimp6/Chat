"""Regression checks for the Logos Pro marketplace exclusion policy."""

import ast
from pathlib import Path

import pytest

import yandex_client_modules.mcp_mixin as mcp_mixin




class CapturingLogosClient(mcp_mixin.YandexMcpMixin):

    def ask(self, message, model_key, conversation_id=None, params=None, execution_trace=None):
        self.last_params = dict(params or {})
        return {"output": []}


def _capture_web_tools(monkeypatch, settings=None, params=None):
    monkeypatch.setattr(mcp_mixin.mcp_storage, "list_servers", lambda **_owner: [])
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
