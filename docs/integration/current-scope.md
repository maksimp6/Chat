# Current-scope integration staging

This is the canonical staging ledger for the focused work coordinated by
[#351](https://github.com/maksimp6/Chat/issues/351). It records dependency and
admission decisions; it is not a replacement for focused pull requests and it
does not authorize automatic merging.

The architecture decisions in
[#350](https://github.com/maksimp6/Chat/issues/350) remain authoritative, while
[#343](https://github.com/maksimp6/Chat/issues/343) coordinates execution. The
detailed ownership and ordering rules live in
[Architecture integration coordination](../architecture/integration-coordination.md).

## Admission rules

A focused pull request may be staged only when it:

1. is updated from current `master` after its dependencies;
2. has completed required review and has green required checks;
3. preserves the host-managed `RuntimeLoader` and dispatcher-owned runtime
   resource model;
4. does not introduce a per-preview Flask process or container, localhost
   proxying, `runtime_port` transport, or process-global preview paths;
5. keeps tool execution behind `UniversalToolExecutor` and runtime resources
   behind `RuntimeDispatcher`; and
6. remains independently reviewable on its own branch.

Failures found in combined testing belong in the focused pull request that owns
the behavior whenever practical. Integration-only workarounds must not be used
to admit red work. Conflict resolutions must follow #350 rather than combine
competing abstractions mechanically.

## Integrated baseline

Runtime foundation [#363](https://github.com/maksimp6/Chat/pull/363) is merged.
It introduced commit `64611a1900891aad0f421c94e73ffc7162942fc3`, containing
the #347 runtime-owner policy and the #349 `RuntimeLoader` foundation. That SHA
is the historical foundation, not the current head.

Repository cleanup on `master` is also authoritative. Integration must not
restore removed root files or `runtime_marker.txt`.

## Integration ledger

Status verified on 2026-09-28 against `master` commit
`db69a2dd1d0213c6b7e01462c1ff21ce1e1a9e6f`. The former candidates below are
already merged; they must not be described as pending staging work.

| Pull request | Merged commit | Integrated responsibility |
| --- | --- | --- |
| [#359](https://github.com/maksimp6/Chat/pull/359) | `e6b2dfe` | Host-managed preview lifecycle on `RuntimeLoader`. |
| [#360](https://github.com/maksimp6/Chat/pull/360) | `b90ac6a` | Degraded frontend shell with repository-local assets. |
| [#361](https://github.com/maksimp6/Chat/pull/361) | `3214182` | Dispatcher-scoped MCP/tool calls. |
| [#366](https://github.com/maksimp6/Chat/pull/366) | `b6ca3ea` | Runtime-scoped storage provider abstraction. |
| [#369](https://github.com/maksimp6/Chat/pull/369) | `e72eed4` | Conversation-scoped agent routing foundation. |
| [#370](https://github.com/maksimp6/Chat/pull/370) | `be17e2f` | Runtime isolation for tool and MCP execution. |
| [#374](https://github.com/maksimp6/Chat/pull/374) | `5f1db0f` | Android release artifact verification. |
| [#377](https://github.com/maksimp6/Chat/pull/377) | `c667e8e` | Scoped plugin execution contract. |
| [#378](https://github.com/maksimp6/Chat/pull/378) | `9cf0b66` | Dispatcher-scoped filesystem isolation. |
| [#382](https://github.com/maksimp6/Chat/pull/382) | `7412314` | Stable stopped-environment gateway status. |
| [#383](https://github.com/maksimp6/Chat/pull/383) | `b177607` | `RuntimeLoader` revision isolation. |
| [#390](https://github.com/maksimp6/Chat/pull/390) | `0e2d0f3` | Local runtime command execution through the accepted boundary. |

The old documentation PRs #364 and #365 were closed without merge. Their
intended ledger/coordination content was superseded by the synchronized docs
now present on `master`; do not revive those branches.

If two candidates materially overlap, select the implementation that consumes
the merged foundation and has the narrowest authoritative ownership boundary.
Document why the other candidate is superseded instead of stacking both.

## Combined validation

After every admitted candidate, record its pull request and exact commit here.
Run its focused tests, then the repository checks:

```console
python -m compileall -q .
python tests/validate_runtime_modules.py
pytest -q
bash scripts/format.sh check
git diff --check
```

Run frontend validation for frontend changes and the prescribed Android unit
test and debug assembly for Android or shared release changes. Schema changes
must validate both SQLite and PostgreSQL. Security-sensitive changes require
authorization, isolation, cleanup, and secret-redaction regression coverage.
