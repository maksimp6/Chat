"""Read-only Dota 2 live data for Alice Pro.

The tool uses OpenDota's public ``/api/live`` endpoint and deliberately keeps
the integration provider-agnostic: Alice receives one normalized snapshot per
tool call, while the caller decides how often to poll.  A small in-process
cache protects the free endpoint from duplicate requests.  If the endpoint is
temporarily unavailable, the last successful snapshot can be returned with an
explicit stale/fallback marker; stale data is never presented as live.

No betting action, bookmaker account, or model call is performed here.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import threading
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from trace_manager import get_current_trace


OPEN_DOTA_LIVE_URL = "https://api.opendota.com/api/live"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_CACHE_TTL_SECONDS = 10.0
MAX_CACHE_TTL_SECONDS = 300.0
MIN_REQUEST_INTERVAL_SECONDS = 5.0


class DotaLiveError(RuntimeError):
    """Base error for the Dota live-data adapter."""


class DotaLiveRateLimitError(DotaLiveError):
    """Raised when a caller asks for a fresh request too soon."""


@dataclass
class _CacheEntry:
    snapshot: dict[str, Any]
    stored_at: float


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _team_names(game: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(game.get("team_name_radiant") or ""),
        str(game.get("team_name_dire") or ""),
    )


def find_live_match(
    games: list[Mapping[str, Any]], teams: list[str] | tuple[str, ...]
) -> Mapping[str, Any] | None:
    """Find the newest live game containing both requested team names."""

    needles = [str(team).strip().casefold() for team in teams if str(team).strip()]
    if len(needles) != 2:
        raise ValueError("exactly two non-empty team names are required")

    candidates: list[Mapping[str, Any]] = []
    for game in games:
        radiant, dire = _team_names(game)
        haystack = f"{radiant} {dire}".casefold()
        if all(needle in haystack for needle in needles):
            candidates.append(game)

    if not candidates:
        return None
    return max(candidates, key=lambda item: _as_int(item.get("last_update_time")) or 0)


def normalize_live_match(
    game: Mapping[str, Any], *, observed_at: str | None = None
) -> dict[str, Any]:
    """Convert an OpenDota live-game object into Alice's stable shape."""

    radiant_name, dire_name = _team_names(game)
    updated = _as_int(game.get("last_update_time"))
    players: list[dict[str, Any]] = []
    for player in game.get("players") or []:
        if not isinstance(player, Mapping):
            continue
        players.append(
            {
                "account_id": player.get("account_id"),
                "hero_id": player.get("hero_id"),
                "team": player.get("team"),
                "team_slot": player.get("team_slot"),
                "name": player.get("name"),
                "team_name": player.get("team_name"),
            }
        )

    return {
        "source": "opendota.live",
        "status": "live",
        "is_live": True,
        "observed_at": observed_at or _now_iso(),
        "match_id": game.get("match_id"),
        "series_id": game.get("series_id"),
        "league_id": game.get("league_id"),
        "game_time": game.get("game_time"),
        "delay": game.get("delay"),
        "last_update_time": updated,
        "last_update_iso": (
            datetime.fromtimestamp(updated, timezone.utc).isoformat() if updated else None
        ),
        "radiant": {
            "name": radiant_name,
            "team_id": game.get("team_id_radiant"),
            "score": game.get("radiant_score"),
        },
        "dire": {
            "name": dire_name,
            "team_id": game.get("team_id_dire"),
            "score": game.get("dire_score"),
        },
        "radiant_lead": game.get("radiant_lead"),
        "building_state": game.get("building_state"),
        "spectators": game.get("spectators"),
        "is_watch_eligible": game.get("is_watch_eligible"),
        "players": players,
    }


class DotaLiveClient:
    """Small cached client for the public OpenDota live endpoint."""

    def __init__(
        self,
        *,
        endpoint: str = OPEN_DOTA_LIVE_URL,
        fetcher: Callable[[float], list[Mapping[str, Any]]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], str] = _now_iso,
        min_request_interval: float = MIN_REQUEST_INTERVAL_SECONDS,
    ) -> None:
        self.endpoint = endpoint
        self._fetcher = fetcher or self._request_live_games
        self._clock = clock
        self._wall_clock = wall_clock
        self._min_request_interval = max(0.0, float(min_request_interval))
        self._cache: dict[tuple[str, ...], _CacheEntry] = {}
        self._last_fetch_at: float | None = None
        self._lock = threading.RLock()

    def _request_live_games(self, timeout: float) -> list[Mapping[str, Any]]:
        trace = get_current_trace()
        started = self._clock()
        request = Request(
            self.endpoint,
            headers={
                "Accept": "application/json",
                "User-Agent": "AlicePro-DotaLive/1.0",
            },
        )
        if trace:
            trace.add_event(
                "provider_api_request",
                {
                    "provider": "opendota",
                    "service": "live",
                    "operation": "get_live_games",
                    "method": "GET",
                    "path": "/api/live",
                },
            )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            if trace:
                trace.add_event(
                    "provider_api_response",
                    {
                        "provider": "opendota",
                        "service": "live",
                        "operation": "get_live_games",
                        "method": "GET",
                        "path": "/api/live",
                        "http_status": getattr(exc, "code", None),
                        "timing_ms": round((self._clock() - started) * 1000, 2),
                        "success": False,
                    },
                )
                trace.record_error(
                    "dota_live.opendota", "OpenDota live request failed", exception=exc
                )
            raise DotaLiveError("OpenDota live request failed") from exc

        if not isinstance(payload, list):
            if trace:
                trace.record_error(
                    "dota_live.opendota", "OpenDota returned an invalid live payload"
                )
            raise DotaLiveError("OpenDota returned an invalid live payload")

        games = [item for item in payload if isinstance(item, Mapping)]
        if trace:
            trace.add_event(
                "provider_api_response",
                {
                    "provider": "opendota",
                    "service": "live",
                    "operation": "get_live_games",
                    "method": "GET",
                    "path": "/api/live",
                    "timing_ms": round((self._clock() - started) * 1000, 2),
                    "success": True,
                    "items": len(games),
                },
            )
        return games

    @staticmethod
    def _cache_key(teams: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(str(team).strip().casefold() for team in teams))

    @staticmethod
    def _validate_ttl(cache_ttl: float | None) -> float:
        value = DEFAULT_CACHE_TTL_SECONDS if cache_ttl is None else float(cache_ttl)
        if value < 0 or value > MAX_CACHE_TTL_SECONDS:
            raise ValueError(f"cache_ttl_seconds must be between 0 and {MAX_CACHE_TTL_SECONDS:g}")
        return value

    def _present(
        self,
        snapshot: dict[str, Any],
        *,
        age_seconds: float,
        cache_hit: bool,
        stale: bool,
        fallback: str | None = None,
        fallback_reason: str | None = None,
        include_players: bool,
    ) -> dict[str, Any]:
        result = deepcopy(snapshot)
        if not include_players:
            result.pop("players", None)
        result["freshness"] = {
            "cache_hit": cache_hit,
            "stale": stale,
            "age_seconds": round(max(0.0, age_seconds), 3),
            "fallback": fallback,
            "fallback_reason": fallback_reason,
        }
        if stale:
            result["status"] = "stale"
            result["is_live"] = False
        return result

    def snapshot(
        self,
        teams: list[str] | tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
        cache_ttl_seconds: float | None = None,
        include_players: bool = False,
    ) -> dict[str, Any]:
        """Return one normalized snapshot or an explicit not-found state."""

        key = self._cache_key(teams)
        if len(key) != 2 or any(not item for item in key):
            raise ValueError("exactly two non-empty team names are required")

        timeout = DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None else float(timeout_seconds)
        if timeout <= 0 or timeout > 30:
            raise ValueError("timeout_seconds must be between 0 and 30")
        cache_ttl = self._validate_ttl(cache_ttl_seconds)

        with self._lock:
            now = self._clock()
            cached = self._cache.get(key)
            if cached is not None:
                age = now - cached.stored_at
                if age <= cache_ttl:
                    return self._present(
                        cached.snapshot,
                        age_seconds=age,
                        cache_hit=True,
                        stale=False,
                        include_players=include_players,
                    )

            if (
                self._last_fetch_at is not None
                and now - self._last_fetch_at < self._min_request_interval
            ):
                if cached is not None:
                    return self._present(
                        cached.snapshot,
                        age_seconds=now - cached.stored_at,
                        cache_hit=True,
                        stale=True,
                        fallback="cache",
                        fallback_reason="request_throttled",
                        include_players=include_players,
                    )
                raise DotaLiveRateLimitError("OpenDota request throttled; retry later")

            self._last_fetch_at = now

        try:
            games = self._fetcher(timeout)
            game = find_live_match(list(games), list(key))
        except DotaLiveError as exc:
            with self._lock:
                cached = self._cache.get(key)
                if cached is not None:
                    return self._present(
                        cached.snapshot,
                        age_seconds=self._clock() - cached.stored_at,
                        cache_hit=True,
                        stale=True,
                        fallback="cache",
                        fallback_reason="primary_unavailable",
                        include_players=include_players,
                    )
            raise exc

        if game is None:
            with self._lock:
                cached = self._cache.get(key)
                if cached is not None:
                    return self._present(
                        cached.snapshot,
                        age_seconds=self._clock() - cached.stored_at,
                        cache_hit=True,
                        stale=True,
                        fallback="cache",
                        fallback_reason="match_not_found",
                        include_players=include_players,
                    )
            return {
                "source": "opendota.live",
                "status": "not_found",
                "is_live": False,
                "observed_at": self._wall_clock(),
                "teams": list(teams),
                "freshness": {
                    "cache_hit": False,
                    "stale": False,
                    "age_seconds": 0,
                    "fallback": None,
                    "fallback_reason": None,
                },
            }

        snapshot = normalize_live_match(game, observed_at=self._wall_clock())
        with self._lock:
            self._cache[key] = _CacheEntry(snapshot=deepcopy(snapshot), stored_at=self._clock())
        return self._present(
            snapshot,
            age_seconds=0,
            cache_hit=False,
            stale=False,
            include_players=include_players,
        )

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._last_fetch_at = None


_DEFAULT_CLIENT = DotaLiveClient()


def dota_live_snapshot(
    arguments: dict[str, Any], cfg: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Alice function: read one free, structured Dota 2 live snapshot."""

    arguments = arguments or {}
    teams = arguments.get("teams")
    if (
        not isinstance(teams, list)
        or len(teams) != 2
        or any(not isinstance(team, str) or not team.strip() for team in teams)
    ):
        return {
            "success": False,
            "error": "teams must contain exactly two non-empty team names",
            "metadata": {"phase": "validation"},
        }

    try:
        snapshot = _DEFAULT_CLIENT.snapshot(
            teams,
            timeout_seconds=arguments.get("timeout_seconds"),
            cache_ttl_seconds=arguments.get("cache_ttl_seconds"),
            include_players=bool(arguments.get("include_players", False)),
        )
    except DotaLiveRateLimitError as exc:
        return {
            "success": False,
            "error": str(exc),
            "metadata": {"phase": "rate_limit", "provider": "opendota.live"},
        }
    except (DotaLiveError, ValueError) as exc:
        return {
            "success": False,
            "error": str(exc),
            "metadata": {"phase": "execution", "provider": "opendota.live"},
        }

    return {
        "success": True,
        "data": snapshot,
        "metadata": {
            "provider": "opendota.live",
            "read_only": True,
            "model_calls": 0,
            "stale": bool(snapshot.get("freshness", {}).get("stale")),
        },
    }


_OPTIONAL_NULL_NUMBER = {
    "anyOf": [
        {"type": "number", "minimum": 0, "maximum": MAX_CACHE_TTL_SECONDS},
        {"type": "null"},
    ]
}


DOTA_LIVE_TOOLS = {
    "dota_live_snapshot": {
        "title": "Dota 2 Live Snapshot",
        "description": (
            "Получить структурированный live-снимок "
            "матча Dota 2 из бесплатного публичного "
            "OpenDota API. Только чтение: без ставок "
            "и аккаунтов букмекеров. При сбое возвращает "
            "явную отметку stale из последнего кэша."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "teams": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "string", "minLength": 1, "maxLength": 100},
                    "description": "Ровно две команды, например Aurora Gaming и Team Liquid.",
                },
                "timeout_seconds": {
                    "anyOf": [
                        {"type": "number", "minimum": 0.1, "maximum": 30},
                        {"type": "null"},
                    ],
                    "description": ("HTTP timeout; null использует значение 10 секунд."),
                },
                "cache_ttl_seconds": {
                    **_OPTIONAL_NULL_NUMBER,
                    "description": "TTL кэша; null использует 10 секунд.",
                },
                "include_players": {
                    "anyOf": [{"type": "boolean"}, {"type": "null"}],
                    "description": (
                        "Включить игроков и героев; по умолчанию false для компактного ответа."
                    ),
                },
            },
            "required": ["teams"],
            "additionalProperties": False,
        },
        "output_schema": {"type": "object"},
        "capabilities": ["dota", "esports", "live_data", "read"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "metadata": {
            "provider": "opendota.live",
            "free": True,
            "fallback": "stale_cache",
            "cache_scope": "process",
            "no_betting": True,
        },
        "func": dota_live_snapshot,
    }
}


__all__ = [
    "DotaLiveClient",
    "DotaLiveError",
    "DotaLiveRateLimitError",
    "DOTA_LIVE_TOOLS",
    "find_live_match",
    "normalize_live_match",
    "dota_live_snapshot",
]
