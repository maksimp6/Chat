# Wikifunctions code reused by Logos

The pinned implementations in `sources.json` are adapted locally under
Apache-2.0; see `LICENSE-2.0.txt`. Each record retains the original source code,
its SHA-256, implementation ID, revision and permanent source URL. The adapted
files identify their changes. No Wikifunctions service is called at runtime.

| Function | Implementation and revision | Local API | Existing callers |
| --- | --- | --- | --- |
| Z21951 Clamp | Z21953, 162115 | `core.wikifunctions.clamp_integer` | File-search result count; HTTP/shell/session timeouts; two Cloud.ru list limits |
| Z12316 regex replacement | Z12317, 285821 | `LogosWikifunctions.replaceRegex` | Browser text whitespace normalization; trace export filenames |
| Z10079 trim | Z10081, 143512 | `LogosWikifunctions.trim` | Browser text whitespace normalization |

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
importable by the Node emulator.

HTML escaping was excluded: Z31475 changes the quote policy, while the
Z31709 composition can normalize newlines. Neither is a drop-in replacement
for the current Logos escape helpers.

Validation for this change is source comparison and independent static review.
Application execution and tests were not run under the repository policy.
