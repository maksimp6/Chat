# Static function inventory for Wikifunctions reuse

**Baseline:** `develop` at `e3f7bfa08267d004b23c5d024aafc38c6e05f1c2`  
**Scan date:** 2026-10-27  
**Baseline scan:** 4,385 first-party Python and JavaScript function definitions; 3 local pinned adapters for 3 Wikifunctions IDs.  
**This branch:** 4,391 definitions; 9 local pinned adapters for 7 Wikifunctions IDs.

## Counts

| Source | Files scanned | Baseline definitions | This branch |
| --- | ---: | ---: | ---: |
| Python | 401 tracked (400 non-empty) | 3,367 | 3,371 |
| JavaScript family | 48 | 1,018 | 1,020 |
| **Total** | **449** | **4,385** | **4,391** |
| Pinned local adapters | — | 3 (0.07%) | **9 (0.20%)** |
| Distinct Wikifunctions IDs | — | 3 (0.07%) | **7 (0.16%)** |

The branch adds six local function definitions: four Python adapters and two JavaScript adapters. The two Python string helpers pin alternate language implementations of Wikifunctions functions already used by the JavaScript adapters, so the branch has nine local implementations for seven distinct Wikifunctions functions.

## Wikifunctions functions marked in source

| Local function | Wikifunctions function | Pinned implementation | Source file |
| --- | --- | --- | --- |
| `clamp_integer` | Z21951 (Clamp) | Z21953, revision 162115 | `core/wikifunctions.py` |
| `trim` | Z10079 (trim) | Z10081, revision 143512 | `static/wikifunctions.js` |
| `trim_string` | Z10079 (trim) | Z10082, revision 143513 | `core/wikifunctions.py` |
| `replaceRegex` | Z12316 (regex replacement) | Z12317, revision 285821 | `static/wikifunctions.js` |
| `mean_float64_list` | Z22583 (mean of float64 list) | Z22587, revision 168295 | `core/wikifunctions.py` |
| `median_float64_list` | Z22588 (median of float64 list) | Z22589, revision 168298 | `core/wikifunctions.py` |
| `lowercase` | Z10047 (lowercase string) | Z10048, revision 207179 | `static/wikifunctions.js` |
| `lowercase_string` | Z10047 (lowercase string) | Z10050, revision 207180 | `core/wikifunctions.py` |
| `uppercase` | Z10018 (uppercase string) | Z10019, revision 216191 | `static/wikifunctions.js` |

All nine local adapters have an adjacent `НЕ РЕДАКТИРОВАТЬ` comment with source and implementation IDs. The mean/median KPI properties delegate to the same standard-library functions they used before and keep their empty-list `None` result. The string helpers call the same native `strip`, `lower`, `upper`, `toLowerCase`, and `toUpperCase` methods as their pinned source implementations.

526 string-helper calls in the PR use shared adapters: 65 JavaScript calls (17 case conversions and 48 trims) plus 461 Python calls (373 trims and 88 lowercases). Two KPI properties also use the pinned mean and median. Nine additional JavaScript `replaceRegex` call sites use Z12316 for HTML escaping (4), trace secret masking (2), browser-host normalization (2), and Dozzle base paths (1). `clamp_integer` also bounds provider log tails, file seek offsets and cached-token counts. Four additional passes processed 274 direct `strip()`/`lower()` occurrences across 69 modules and added 242 adapter calls after consolidating repeated normalization. Cloud.ru IAM and secret-management values, provider credentials, project/user IDs, image and embedding inputs, agent capabilities, review-policy and sensitive trace keys, runtime endpoints, Termux command output and RDC Android identifiers now normalize once. The synchronous `static/boot.js` bootstrap keeps native `trim()` because the shared browser bundle loads after it.

The separate 100-candidate inventory remains a list of possible unifications, not a claim that all 100 candidates are from Wikifunctions or can be replaced. JSON/map helpers remain unadopted: strict duplicate-key rejection and first-match map lookup have different contracts. This change applies only where the pinned source operation matches current behavior.

## Method and limits

The baseline scan covered tracked first-party `.py`, `.js`, `.cjs`, `.mjs`, `.jsx`, `.ts` and `.tsx` files. It counted Python `def`/`async def` declarations, including methods. In JavaScript-family files it counted function-keyword declarations/expressions, arrow functions, and method-shorthand definitions after ignoring comments and string literals. It excluded `vendor`, `node_modules`, `third_party`, tests/`__tests__`, and `.agents`, as well as files in other languages. This is a syntactic count, not a runtime or semantic count.

Tests and application execution were not run under the repository policy. Review was limited to the source and documentation diff.
