from __future__ import annotations

import io
import json
import sys
import unittest
from unittest.mock import patch

from dota.live_tools import (
    DotaLiveClient,
    DotaLiveError,
    DotaLiveRateLimitError,
    _as_int,
    dota_live_snapshot,
    find_live_match,
    normalize_live_match,
)
from tool_registry import ToolRegistry, registry
from trace_manager import ExecutionTrace, _current_trace
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


class JsonResponse:
    def __init__(self, payload):
        self._stream = io.StringIO(json.dumps(payload))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *args, **kwargs):
        return self._stream.read(*args, **kwargs)


class DotaLiveToolsTests(unittest.TestCase):
    def test_find_live_match_requires_both_teams_and_picks_newest(self):
        older = game(last_update_time=10)
        newer = game(last_update_time=20)
        self.assertIs(find_live_match([older, newer], ["Aurora", "Liquid"]), newer)
        self.assertIsNone(find_live_match([older], ["Aurora", "Spirit"]))
        with self.assertRaises(ValueError):
            find_live_match([older], ["Aurora"])

    def test_helpers_handle_invalid_numeric_values_and_player_rows(self):
        self.assertIsNone(_as_int(True))
        self.assertIsNone(_as_int("not-a-number"))
        snapshot = normalize_live_match({**game(), "players": [None]})
        self.assertEqual(snapshot["players"], [])

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
        with_players = client.snapshot(["Aurora", "Liquid"], include_players=True)
        self.assertEqual(with_players["players"][0]["hero_id"], 2)

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

    def test_client_returns_stale_cache_when_request_is_throttled(self):
        client = DotaLiveClient(fetcher=lambda _timeout: [game()], min_request_interval=60)
        client.snapshot(["Aurora", "Liquid"], cache_ttl_seconds=0)
        stale = client.snapshot(["Aurora", "Liquid"], cache_ttl_seconds=0)
        self.assertEqual(stale["freshness"]["fallback_reason"], "request_throttled")

    def test_client_returns_not_found_without_a_cache(self):
        client = DotaLiveClient(fetcher=lambda _timeout: [], min_request_interval=0)
        result = client.snapshot(["Aurora", "Liquid"], cache_ttl_seconds=0)
        self.assertEqual(result["status"], "not_found")
        self.assertFalse(result["is_live"])

    def test_client_returns_stale_cache_when_match_disappears(self):
        responses = [[game()], []]

        def fetcher(_timeout):
            return responses.pop(0)

        client = DotaLiveClient(fetcher=fetcher, min_request_interval=0)
        client.snapshot(["Aurora", "Liquid"], cache_ttl_seconds=0)
        stale = client.snapshot(["Aurora", "Liquid"], cache_ttl_seconds=0)
        self.assertEqual(stale["freshness"]["fallback_reason"], "match_not_found")

    def test_client_propagates_primary_error_without_a_cache(self):
        def fetcher(_timeout):
            raise DotaLiveError("network down")

        client = DotaLiveClient(fetcher=fetcher, min_request_interval=0)
        with self.assertRaises(DotaLiveError):
            client.snapshot(["Aurora", "Liquid"], cache_ttl_seconds=0)

    def test_client_validates_timeout_and_cache_ttl(self):
        client = DotaLiveClient(fetcher=lambda _timeout: [game()], min_request_interval=0)
        with self.assertRaises(ValueError):
            client.snapshot(["Aurora", "Liquid"], timeout_seconds=31)
        with self.assertRaises(ValueError):
            client.snapshot(["Aurora", "Liquid"], cache_ttl_seconds=301)
        client.clear_cache()

    def test_http_success_records_provider_trace(self):
        trace = ExecutionTrace()
        token = _current_trace.set(trace)
        try:
            client = DotaLiveClient(fetcher=None, min_request_interval=0)
            with patch("dota.live_tools.urlopen", return_value=JsonResponse([game()])):
                games = client._request_live_games(1)
        finally:
            _current_trace.reset(token)

        self.assertEqual(len(games), 1)
        event_types = [event["type"] for event in trace.trace["events"]]
        self.assertIn("provider_api_request", event_types)
        self.assertIn("provider_api_response", event_types)

    def test_http_failure_records_provider_error(self):
        trace = ExecutionTrace()
        token = _current_trace.set(trace)
        try:
            client = DotaLiveClient(fetcher=None, min_request_interval=0)
            with patch("dota.live_tools.urlopen", side_effect=OSError("down")):
                with self.assertRaises(DotaLiveError):
                    client._request_live_games(1)
        finally:
            _current_trace.reset(token)

        self.assertTrue(trace.trace["errors"])

    def test_http_invalid_payload_records_provider_error(self):
        trace = ExecutionTrace()
        token = _current_trace.set(trace)
        try:
            client = DotaLiveClient(fetcher=None, min_request_interval=0)
            with patch("dota.live_tools.urlopen", return_value=JsonResponse({"games": []})):
                with self.assertRaises(DotaLiveError):
                    client._request_live_games(1)
        finally:
            _current_trace.reset(token)

        self.assertTrue(trace.trace["errors"])

    def test_tool_rejects_invalid_team_count(self):
        result = dota_live_snapshot({"teams": ["Aurora"]})
        self.assertFalse(result["success"])
        self.assertEqual(result["metadata"]["phase"], "validation")

    def test_tool_exposes_rate_limit_and_execution_errors(self):
        rate_limited = DotaLiveClient(
            fetcher=lambda _timeout: (_ for _ in ()).throw(DotaLiveRateLimitError("slow down")),
            min_request_interval=0,
        )
        with patch("dota.live_tools._DEFAULT_CLIENT", rate_limited):
            result = dota_live_snapshot({"teams": ["Aurora", "Liquid"]})
        self.assertEqual(result["metadata"]["phase"], "rate_limit")

        failed = DotaLiveClient(
            fetcher=lambda _timeout: (_ for _ in ()).throw(DotaLiveError("down")),
            min_request_interval=0,
        )
        with patch("dota.live_tools._DEFAULT_CLIENT", failed):
            result = dota_live_snapshot({"teams": ["Aurora", "Liquid"]})
        self.assertEqual(result["metadata"]["phase"], "execution")

    def test_registry_exposes_dota_tool_with_read_only_policy(self):
        self.assertIn("dota_live_snapshot", registry.get_available_categories()["dota"])
        definition = registry.get_universal_definition("dota_live_snapshot")
        self.assertIsNotNone(definition)
        self.assertTrue(definition.read_only)
        self.assertFalse(definition.requires_approval)
        self.assertIn("mcp", definition.supported_transports)

    def test_registry_skips_dota_module_when_import_fails(self):
        with patch.dict(sys.modules, {"dota.live_tools": None}):
            isolated = ToolRegistry()
        self.assertNotIn("dota", isolated.get_available_categories())

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
