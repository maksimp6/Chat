# Dota 2 live data

Issue: [#705](https://github.com/maksimp6/Chat/issues/705)

Alice Pro exposes the read-only `dota_live_snapshot` function through the
existing Universal Tool Registry, Responses API, local-agent path, and MCP.
The function reads the free public OpenDota `GET /api/live` feed and returns a
normalized snapshot for exactly two requested teams.

## Contract

```json
{
  "teams": ["Aurora Gaming", "Team Liquid"],
  "timeout_seconds": null,
  "cache_ttl_seconds": null,
  "include_players": false
}
```

Only `teams` is required. Optional values are bounded by the tool schema. The
default response includes score, game time, delay, team IDs, net-worth lead,
building state, and match/series/league identifiers. Player and hero rows are
opt-in because they make a larger model context.

## Availability and limits

- The adapter is read-only and does not place or recommend a real-money bet.
- A process-local cache defaults to 10 seconds; requests are throttled to at
  most one fresh OpenDota request every 5 seconds.
- When the primary endpoint fails, the last successful snapshot may be returned
  with `status: "stale"`, `is_live: false`, and
  `freshness.fallback: "cache"`. The caller must not present stale data as a
  current score.
- If there is no cached snapshot, the tool returns a structured error or a
  successful `status: "not_found"` result when no matching live game exists.
- OpenDota's upstream delay is surfaced unchanged in the `delay` field; Alice
  should use a separate stream frame only when current visual evidence is
  required.

The tool itself makes no model request. Polling cadence is controlled by the
caller/scheduler, so a chat turn can request one compact snapshot instead of
keeping a blocking watcher inside the tool executor.
