# Static function inventory for Wikifunctions reuse

**Baseline:** `develop` at `e3f7bfa08267d004b23c5d024aafc38c6e05f1c2`  
**Scan date:** 2026-10-27  
**Baseline scan:** 4,385 first-party Python and JavaScript function definitions; 3 pinned Wikifunctions implementations.  
**This branch:** 4,387 definitions; 5 pinned Wikifunctions implementations (0.11%).

## Counts

| Source | Files scanned | Baseline definitions | This branch |
| --- | ---: | ---: | ---: |
| Python | 401 tracked (400 non-empty) | 3,367 | 3,369 |
| JavaScript family | 48 | 1,018 | 1,018 |
| **Total** | **449** | **4,385** | **4,387** |
| Pinned Wikifunctions adaptations | — | 3 | **5 (0.11%)** |

The branch count adds the two new Python adapters below to the statically scanned baseline; no other definitions were added.

## Wikifunctions functions marked in source

| Local function | Wikifunctions function | Pinned implementation | Source file |
| --- | --- | --- | --- |
| `clamp_integer` | Z21951 (Clamp) | Z21953, revision 162115 | `core/wikifunctions.py` |
| `trim` | Z10079 (trim) | Z10081, revision 143512 | `static/wikifunctions.js` |
| `replaceRegex` | Z12316 (regex replacement) | Z12317, revision 285821 | `static/wikifunctions.js` |
| `mean_float64_list` | Z22583 (mean of float64 list) | Z22587, revision 168295 | `core/wikifunctions.py` |
| `median_float64_list` | Z22588 (median of float64 list) | Z22589, revision 168298 | `core/wikifunctions.py` |

Each pinned implementation has an adjacent `НЕ РЕДАКТИРОВАТЬ` comment with its source and implementation ID. The two new adapters delegate to the same Python standard-library functions used by their pinned Wikifunctions implementations. Existing empty-list guards remain in the KPI properties, preserving their `None` result.

The separate 100-candidate inventory represents possible future unifications. It does not mean that all 100 candidates are sourced from Wikifunctions or need replacement; the two statistics candidates already used Python's standard `statistics` functions before this change.

## Method and limits

The baseline scan covered tracked first-party `.py`, `.js`, `.cjs`, `.mjs`, `.jsx`, `.ts` and `.tsx` files. It counted Python `def`/`async def` declarations, including methods. In JavaScript-family files it counted function-keyword declarations/expressions, arrow functions, and method-shorthand definitions after ignoring comments and string literals. It excluded `vendor`, `node_modules`, `third_party`, tests/`__tests__`, and `.agents`, as well as files in other languages. This is a syntactic count, not a runtime or semantic count.

Tests and application execution were not run under the repository policy. Review was limited to the source and documentation diff.
