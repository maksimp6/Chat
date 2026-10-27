# Static function inventory for Wikifunctions reuse

**Snapshot:** `develop` at `e3f7bfa08267d004b23c5d024aafc38c6e05f1c2`  
**Scan date:** 2026-10-27  
**Result:** 4,385 first-party Python and JavaScript function definitions; 3 pinned Wikifunctions implementations (0.07%).

## Counts

| Source | Files scanned | Counted definitions | Counting rule |
| --- | ---: | ---: | --- |
| Python | 401 tracked (400 non-empty) | 3,367 | `def` and `async def`, including methods |
| JavaScript family | 48 | 1,018 | Function declarations/expressions (868), arrow functions (74), and method shorthand (76) |
| **Total** | **449** | **4,385** | **Python and JavaScript family only** |
| Pinned Wikifunctions adaptations | — | **3 (0.07%)** | Matched against `third_party/wikifunctions/sources.json` |

## Wikifunctions functions marked in source

| Local function | Wikifunctions function | Pinned implementation | Source file |
| --- | --- | --- | --- |
| `clamp_integer` | Z21951 (Clamp) | Z21953, revision 162115 | `core/wikifunctions.py` |
| `trim` | Z10079 (trim) | Z10081, revision 143512 | `static/wikifunctions.js` |
| `replaceRegex` | Z12316 (regex replacement) | Z12317, revision 285821 | `static/wikifunctions.js` |

Each implementation now has a directly adjacent source comment containing **«НЕ РЕДАКТИРОВАТЬ»**. The change only adds comments and inventory documentation; function bodies and callers are unchanged.

The separate 100-candidate inventory represents possible future unifications. It is not a claim that all 100 are sourced from Wikifunctions or already implemented from them. This scan found three pinned adaptations in the repository snapshot.

## Method and limits

The scan covered tracked first-party `.py`, `.js`, `.cjs`, `.mjs`, `.jsx`, `.ts` and `.tsx` files. It counted Python `def`/`async def` declarations, including class methods. In JavaScript-family files it counted function-keyword declarations/expressions, arrow functions, and method-shorthand definitions after ignoring comments and string literals. It excluded `vendor`, `node_modules`, `third_party`, tests/ `__tests__`, and `.agents`, as well as files in other languages. This is a syntactic static count; it does not resolve dynamically generated functions or decide whether functions are semantically equivalent.

Tests and application execution were not run under the repository policy. Review performed here is limited to the source and documentation diff.
