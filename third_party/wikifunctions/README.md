# Wikifunctions code reused by Logos

The pinned implementations in `sources.json` are adapted locally under
Apache-2.0; see `LICENSE-2.0.txt`. Each record retains the original source code,
its SHA-256, implementation ID, revision and permanent source URL. The adapted
files identify their changes. No Wikifunctions service is called at runtime.

| Function | Implementation and revision | Local API | Existing callers |
| --- | --- | --- | --- |
| Z21951 Clamp | Z21953, 162115 | `core.wikifunctions.clamp_integer` | File-search result count; HTTP/shell/session timeouts; two Cloud.ru list limits |
| Z12316 regex replacement | Z12317, 285821 | `LogosWikifunctions.replaceRegex` | Browser text whitespace normalization; trace export filenames; UI escaping; trace-secret masking; browser-host normalization; Dozzle base path |
| Z10079 trim | Z10081, 143512 | `LogosWikifunctions.trim` | Browser text whitespace normalization; UI forms; Project Board inputs |
| Z10079 trim (Python) | Z10082, 143513 | `core.wikifunctions.trim_string` | Conversation titles; browser data; CLI/MCP validation; agent memory/context; SSH and secret configuration; Cloud.ru IAM/secret management/object storage/billing; printing services and audits; voice/file/runtime text; partner CRM; provider credential routes; image/embedding validation; review-policy and trace filters; MCP server URLs; Termux and RDC tools; cloud/provider and quota normalization; plugin/skill metadata; UI form fields |
| Z10047 lowercase (Python) | Z10050, 207180 | `core.wikifunctions.lowercase_string` | Agent memory keys; browser/provider/quota/theme normalization; CLI confirmation and cloud operations; Cloud.ru headers/statuses; printing archive formats; cloud log queries; review-policy file and trace filters; retrieval tokens; HTTP MIME and language codes; GitHub observer/log keys |
| Z10047 lowercase string | Z10048, 207179 | `LogosWikifunctions.lowercase` | UI status/role normalization; browser emulator |
| Z10018 uppercase string | Z10019, 216191 | `LogosWikifunctions.uppercase` | Trace labels; browser emulator |
| Z22583 mean of float64 list | Z22587, 168295 | `core.wikifunctions.mean_float64_list` | Agent office reviews per merge |
| Z22588 median of float64 list | Z22589, 168298 | `core.wikifunctions.median_float64_list` | Agent office lead time |

## Shared operations

526 string-helper calls in the PR use shared adapters: 65 JavaScript calls (17 case conversions and 48 trims) plus 461 Python calls (373 trims and 88 lowercases). Two KPI properties also use the pinned mean and median. Nine additional JavaScript `replaceRegex` call sites use Z12316 for HTML escaping (4), trace secret masking (2), browser-host normalization (2), and Dozzle base paths (1). `clamp_integer` also bounds provider log tails, file seek offsets and cached-token counts. Four additional passes processed 274 direct `strip()`/`lower()` occurrences across 69 modules and added 242 adapter calls after consolidating repeated normalization. Cloud.ru IAM and secret-management values, provider credentials, project/user IDs, image and embedding inputs, agent capabilities, review-policy and sensitive trace keys, runtime endpoints, Termux command output and RDC Android identifiers now normalize once. The synchronous `static/boot.js` bootstrap keeps native `trim()` because the shared browser bundle loads after it.

## Static function inventory

At baseline `develop` commit `e3f7bfa08267d004b23c5d024aafc38c6e05f1c2`, a source-only scan counted **4,385 function definitions**: 3,367 Python and 1,018 JavaScript. This change adds six local adapters (four Python and two JavaScript), bringing the branch count to **4,391** (3,371 Python and 1,020 JavaScript). They represent **7 distinct Wikifunctions IDs** and **9 pinned local implementations** (0.16% and 0.20% of definitions, respectively). The separate 100-candidate inventory is not a count of adopted implementations.

The scan covered 401 tracked `.py` files (400 non-empty) and 48 `.js`, `.cjs`, `.mjs`, `.jsx`, `.ts` or `.tsx` files. It counted Python `def`/`async def` declarations, JavaScript `function` declarations/expressions, arrow functions and method-shorthand definitions. It excluded `vendor`, `node_modules`, `third_party`, tests/ `__tests__`, `.agents`, and all other languages. This is a syntactic source count, not a runtime or semantic count. See [the audit details](../../docs/audits/2026-10-27-wikifunctions-static-function-inventory.md).

## Preserved behavior

Clamp is used only after the existing `int(...)` conversions, with the same
constant ordered bounds. For integers below the lower bound, within the bounds,
and above the upper bound, its branches return the same values as the previous
`max(lower, min(value, upper))`. Defaults, exception handlers and bounds remain
at their original call sites. The catalog's natural-number function is adapted
to these existing integer inputs, including negative values clamped to the
positive lower bound; floats and JavaScript clamp operations are not migrated.

The JavaScript functions use the same ECMAScript `replace`, `RegExp` and `trim`
operations. Callers retain their original `String(value || fallback)` conversion,
patterns and `g` flag. Trace filenames still apply `.slice(0, 80) || "trace"`.
The shared JavaScript file is served before callers and is also directly
importable by the Node emulator. The lowercase and uppercase adapters call the
same ECMAScript string operations as their pinned source implementations.

The Wikifunctions mean and median implementations delegate to Python's
`statistics.mean` and `statistics.median`; the agent KPI properties retain their
existing empty-list `None` behavior. Python and JavaScript trim/lowercase/uppercase
adapters call the same native string methods as their pinned sources, preserving
the existing conversions at callers.

The local HTML escape helper now reuses Z12316's regex replacement with the
same four ordered global patterns, preserving its quote policy. Z31475 still
changes quote handling, and the Z31709 composition can normalize newlines, so
neither is used as a drop-in replacement.

Validation for this change is source comparison and independent static review.
Application execution and tests were not run under the repository policy.
