"""End-to-end chat skill loading regression coverage."""

from unittest.mock import patch

import pytest

import mcp_routes
from agent_skills.registry import SkillRegistryError
from app import app


def test_requested_skill_normalization_variants():
    assert mcp_routes._normalize_requested_skills({}, {"skills": None}) == []
    assert mcp_routes._normalize_requested_skills({}, {"skills": "docs-sync"}) == ["docs-sync"]

    with pytest.raises(SkillRegistryError):
        mcp_routes._normalize_requested_skills({"skills": 123}, {})


def test_skill_catalog_endpoint_filters_by_role_and_rejects_unknown_role():
    app.config["TESTING"] = True
    with app.test_client() as client:
        response = client.get("/api/skills?role=docs-engineer")
        invalid = client.get("/api/skills?role=mystery-wizard")

    assert response.status_code == 200
    assert [skill["name"] for skill in response.get_json()["skills"]] == [
        "docs-sync",
        "issue-to-pr",
    ]
    assert invalid.status_code == 400
    assert invalid.get_json()["error"] == "invalid_role"


def test_chat_lazily_loads_selected_skill_into_instructions_and_trace():
    captured = {}

    class FakeClient:
        def ask_with_mcp(self, message, model_key, conversation_id, params, trace=None):
            captured["instructions"] = params.get("instructions")
            return {"output": [], "usage": {}}

        @staticmethod
        def extract_reasoning_and_text(_response):
            return "", "reply"

        @staticmethod
        def extract_usage(_response):
            return None

    with (
        patch.object(mcp_routes, "AliceClient", lambda _config: FakeClient()),
        patch.object(mcp_routes, "get_conv_settings", return_value={}),
        patch.object(mcp_routes, "add_message"),
        patch.object(
            mcp_routes,
            "settle_billing_to_treasury",
            return_value={"status": "not_applicable"},
        ),
        patch.object(mcp_routes, "persist_invocation_trace"),
        patch.object(mcp_routes, "finish_invocation"),
        patch.object(mcp_routes, "get_conversation_title", return_value=None),
    ):
        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": "skill-chat-test",
                    "message": "check this pull request",
                    "role": "release-manager",
                    "skills": ["github-pr-readiness"],
                    "params": {"instructions": "Base instructions."},
                },
            )

    assert response.status_code == 200
    payload = response.get_json()
    assert "Base instructions." in captured["instructions"]
    assert "### Skill: github-pr-readiness" in captured["instructions"]
    assert [entry["name"] for entry in payload["trace"]["skills"]] == ["github-pr-readiness"]
    assert payload["skills"][0]["name"] == "github-pr-readiness"


def test_chat_rejects_skill_outside_role_policy():
    with (
        patch.object(mcp_routes, "get_conv_settings", return_value={}),
        patch.object(mcp_routes, "add_message"),
        patch.object(mcp_routes, "persist_invocation_trace"),
        patch.object(mcp_routes, "fail_invocation"),
    ):
        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": "skill-policy-test",
                    "message": "review security",
                    "role": "docs-engineer",
                    "skills": ["security-review"],
                },
            )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"] == "invalid_skill_selection"
    assert payload["trace"]["errors"][0]["source"] == "skills"
