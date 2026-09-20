# Frontend loading variants

The production route `/` remains unchanged. The following opt-in route is for
the loading experiment described in issue #227 and PR #228:

```text
/loading-experiment?variant=modular
/loading-experiment?variant=core
/loading-experiment?variant=single
/loading-experiment?variant=packed
```

## Variants

- `modular` is the control. It renders the normal server HTML shell and keeps
  CSS and JavaScript as static resources.
- `core` is the proposed simple production shape: the small core scripts are
  embedded in the HTML response, while feature modules are loaded in order
  after the first frame. The module URLs are exposed as
  `window.__ALICE_OPTIONAL_MODULES` for measurement.
- `single` inlines locally served CSS and JavaScript into the same HTML
  response. It is a request-count and latency experiment, not the production
  default.
- `packed` applies the same core-first structure, then embeds the resulting
  HTML as a gzip/base64 payload and uses the browser `DecompressionStream` API
  to restore it. A visible fallback remains available if JavaScript or
  decompression fails.

The packed variant is intentionally experimental. Its payload is already
trusted application markup; user data must continue to enter through the
existing escaped/safe rendering paths. It must be compared against ordinary
HTTP Brotli/gzip compression, because client-side decompression can delay the
first usable render even when it reduces the payload representation.

Measure every variant with the same runner and record the variant from the
`X-Alice-Loading-Variant` response header. At minimum capture transferred
bytes, navigation commit, FCP, LCP, first usable UI, DOMContentLoaded, blank
state duration, decompression time for `packed`, errors, and long tasks.
