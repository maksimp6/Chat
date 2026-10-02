from __future__ import annotations

import unittest
from unittest.mock import patch

from dota.live_tools import (
    DotaLiveClient,
    DotaLiveError,
    DotaLiveRateLimitError,
    dota_live_snapshot,
    find_live_match,
    normalize_live_match,
)
from tool_registry import registry
from universal_tool_platform import UniversalToolExecutor, UniversalToolCall


def game(
    *,
    radiant_score=10,
    dire_score=8,
    last_update_time=100,
    radiant="Team Liquid",
    dire="Aurora Gaming",
):
    return {
        "match_id": "m-1",
        "series_id": "s-1",
        "league_id": "l-1",
        "game_time": 123,
        "delay": 900,
        "last_update_time": last_update_time,
        "team_name_radiant": radiant,
        "team_name_dire": dire,
        "team_id_radiant": 1,
        "team_id_dire": 2,
        "radiant_score": radiant_score,
        "dire_score": dire_score,
        "radiant_lead": 2500,
        "building_state": 3,
        "spectators": 12,
        "players": [{"account_id": 1, "hero_id": 2, "name": "player"}],
    }


class DotaLiveToolsTests(unittest.TestCase):
    def test_find_live_match_requires_both_teams_and_picks_newest(self):
        older = game(last_update_time=10)
        newer = game(last_update_time=20)
        self.assertIs(find_live_match([older, newer], ["Aurora", "Liquid"]), newer)
        self.assertIsNone(find_live_match([older], ["Aurora", "Spirit"]))

    def test_normalize_live_match_has_stable_shape(self):
        snapshot = normalize_live_match(game())
        self.assertEqual(snapshot["status"], "live")
        self.assertTrue(snapshot["is_live"])
        self.assertEqual(snapshot["radiant"], {"name": "Team Liquid", "team_id": 1, "score": 10})
        self.assertEqual(snapshot["dire"]["name"], "Aurora Gaming")
        self.assertEqual(snapshot["players"][0]["hero_id"], 2)

    def test_client_uses_cache_without_duplicate_fetches(self):
        calls = []

        def fetcher(_timeout):
            calls.append(True)
            return [game()]

        client = DotaLiveClient(fetcher=fetcher, min_request_interval=0)
        first = client.snapshot(["Aurora", "Liquid"])
        second = client.snapshot(["Aurora", "Liquid"])

        self.assertEqual(len(calls), 1)
        self.assertFalse(first["freshness"]["cache_hit"])
        self.assertTrue(second["freshness"]["cache_hit"])
        self.assertNotIn("players", second)

    def test_client_returns_stale_cache_when_primary_fails(self):
        responses = [[game()], DotaLiveError("network down")]

        def fetcher(_timeout):
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        client = DotaLiveClient(fetcher=fetcher, min_request_interval=0)
        client.snapshot(["Aurora", "Liquid"], cache_ttl_seconds=0)
        stale = client.snapshot(["Aurora", "Liquid"], cache_ttl_seconds=0)

        self.assertEqual(stale["status"], "stale")
        self.assertFalse(stale["is_live"])
        self.assertEqual(stale["freshness"]["fallback"], "cache")
        self.assertEqual(stale["freshness"]["fallback_reason"], "primary_unavailable")

    def test_client_throttles_without_a_cache(self):
        client = DotaLiveClient(fetcher=lambda _timeout: [game()], min_request_interval=60)
        client.snapshot(["Aurora", "Liquid"], cache_ttl_seconds=0)
        with self.assertRaises(DotaLiveRateLimitError):
            client.snapshot(["Aurora", "Spirit"], cache_ttl_seconds=0)

    def test_tool_rejects_invalid_team_count(self):
        result = dota_live_snapshot({"teams": ["Aurora"]})
        self.assertFalse(result["success"])
        self.assertEqual(result["metadata"]["phase"], "validation")

    def test_registry_exposes_dota_tool_with_read_only_policy(self):
        self.assertIn("dota_live_snapshot", registry.get_available_categories()["dota"])
        definition = registry.get_universal_definition("dota_live_snapshot")
        self.assertIsNotNone(definition)
        self.assertTrue(definition.read_only)
        self.assertFalse(definition.requires_approval)
        self.assertIn("mcp", definition.supported_transports)

    def test_responses_executor_runs_dota_tool_through_registry(self):
        fake_client = DotaLiveClient(fetcher=lambda _timeout: [game()], min_request_interval=0)
        with patch("dota.live_tools._DEFAULT_CLIENT", fake_client):
            result = UniversalToolExecutor(registry).execute(
                UniversalToolCall(
                    tool_name="dota_live_snapshot",
                    arguments={"teams": ["Aurora", "Liquid"]},
                    transport="responses_api",
                )
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["radiant"]["score"], 10)


if __name__ == "__main__":
    unittest.main()
