# `browser`

Browser capabilities: contracts shared by the cloud and local browsing adapters, an adapter backed by the Node.js browser emulator, deterministic parallel browser-role orchestration and semantic synthesis of its results, and the versatile browser the model drives through the shared tool registry (category `browser`, also over MCP): a pure-Python HTTP engine and an optional Chrome engine (DevTools Protocol).

## Public API

`from browser import …`: `BrowserDagBudget`, `BrowserDagResult`, `BrowserRoleDag`, `BrowserRoleTask`, `BrowserTaskBudget`, `BrowserTaskResult`, `Evidence`, `MergedEvidence`, `SemanticDiff`, `SemanticNode`, `SemanticSnapshot`, `build_semantic_snapshot`, `diff_semantic_snapshots`, `merge_evidence`, `BrowserSynthesisPolicy`, `BrowserSynthesisResult`, `EscalationDecision`, `SemanticBrowserWorker`, `synthesize_browser_dag`; `BROWSER_TOOLS` and `BrowserError` load on first use (`import browser` does not import `requests`).

`from browser.tools import BROWSER_TOOLS`: the 17 tool definitions (strict JSON schemas, Russian descriptions, metadata and functions).

| Module | Purpose |
| --- | --- |
| [`browser.adapters`](adapters.py) | Adapter boundary for Logos's cloud and local browser capabilities. |
| [`browser.capabilities`](capabilities.py) | Contracts shared by Logos's cloud and local browsing adapters. |
| [`browser.emulator_adapter`](emulator_adapter.py) | Browser adapter backed by the lightweight Node.js browser emulator. |
| [`browser.orchestration`](orchestration.py) | Deterministic parallel browser-role DAG orchestration. |
| [`browser.pipeline`](pipeline.py) | Semantic synthesis and cheap-first escalation for BrowserRoleDag results. |
| [`browser.semantic`](semantic.py) | Token-efficient semantic browser state and evidence contracts. |
| [`browser.tools`](tools.py) | `BROWSER_TOOLS`: the 17 browser tools of the registry; maps `BrowserError` to failed results. |
| [`browser.context`](context.py) | Call context of one tool call: trace events, sensitive values, session key (dialog, MCP → `default`, explicit `session`). |
| [`browser.operations`](operations.py) | One operation per tool: arguments, engines, page results, pagination, notes. |
| [`browser.sessions`](sessions.py) | Session store and `BrowserSession`: tabs, engine switching, cookie ownership, Chrome lifecycle, abort and reset. |
| [`browser.errors`](errors.py) | `BrowserError` kinds and phases, `Deadline`, the Russian message catalog. |
| [`browser.engine`](engine.py) | The tab-engine interface (`TabEngine`) and its shared dataclasses. |
| [`browser.settings`](settings.py) | Per-session settings, user-agent presets, client hints, Chrome options. |
| [`browser.redact`](redact.py) | Masking of credentials in URLs, headers and text; fingerprints. |
| [`browser.paging`](paging.py) | `paginate` (offset, `max_chars`, `next_offset`), argument helpers, filters, ref parsing. |
| [`browser.dom`](dom.py) | Tolerant iterative HTML/XML tree builder on `HTMLParser`, serializer. |
| [`browser.css`](css.py) | CSS selector subset of the http engine. |
| [`browser.page`](page.py) | `PageModel`: refs L/B/I/F/T/M, roles, accessible names, field state, targets, main text. |
| [`browser.forms`](forms.py) | Form association, field lookup, the WHATWG entry list, submissions. |
| [`browser.metadata`](metadata.py) | Metadata, Open Graph, feeds, JSON-LD, microdata. |
| [`browser.feeds`](feeds.py) | RSS, Atom, RDF and JSON Feed parsing. |
| [`browser.readable`](readable.py) | Markdown and plain text with inline refs; Markdown tables. |
| [`browser.views`](views.py) | `ViewPage`, every view, find, extract, JSON paths, change lists. |
| [`browser.charsets`](charsets.py) | Content classification, sniffing, charset detection and decoding. |
| [`browser.guard`](guard.py) | Link-local guard (fail-closed) for requests, redirects and Chrome requests. |
| [`browser.files`](files.py) | Download and capture destinations, atomic writes, safe file names, free disk. |
| [`browser.http_engine`](http_engine.py) | `HttpClient` on `requests`: browser-like headers, guarded redirect loop, deadlines, streaming, forms, cookies. |
| [`browser.http_tab`](http_tab.py) | `HttpTabEngine`: history with compressed copies, field state, clicks and submissions without JavaScript. |
| [`browser.search`](search.py) | DuckDuckGo (HTML and Lite), Bing, both, or a custom search template. |
| [`browser.chrome_launch`](chrome_launch.py) | Chrome discovery, launch with a fresh profile and minimal environment, shutdown, orphan sweep. |
| [`browser.cdp`](cdp.py) | DevTools Protocol connection over the `websockets` synchronous client. |
| [`browser.chrome`](chrome.py) | `ChromeBrowser` and `ChromeTabEngine`: guarded requests, actions, waits, JavaScript, screenshots, PDF, downloads, dialogs. |
| [`chrome_page.js`](chrome_page.js) | Page helper in the isolated world "logos": ref stamping, visibility and field state, serialization, waits, value serializer. |
| [`browser.host`](host.py) | Ports of the unit (see below). |

## Ports

Everything `browser` uses from other units is a field of `Host` in [`browser/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `resolve_tool_path` | `tool_providers.filesystem.resolve_tool_path` |
| `resolves_to_link_local` | `tool_providers.filesystem.resolves_to_link_local` |

## Install

- `composition/wiring.py` registers the `browser` host factory (`register_all()`, or `register_units("browser")` for this unit alone); the host is built on first use.
- `composition/tool_sources.py` adds the `browser` tool category from `browser.tools`, after `cloud`.
- The http engine needs only `requests` (already a dependency). The Chrome engine is optional and needs Chrome or Chromium plus the Python package `websockets` 13 or newer:
  - Termux: `pkg install x11-repo && pkg install chromium` (≈300–400 MB, more than the Redmi 9 has free, so on the phone Chrome is effectively unavailable and the http engine does the work);
  - Debian/Ubuntu: `sudo apt install chromium`;
  - any other binary: set `LOGOS_BROWSER_CHROME=/path/to/chrome` in `core/.env` (the phone starts the app with `env -i`, so a shell `export` does not reach it) and restart Logos.
- Chrome discovery, evaluated on every launch: `LOGOS_BROWSER_CHROME` (an absolute path or a name on `PATH`; a bad value is reported, with no fallback), then `chromium`, `chromium-browser`, `google-chrome`, `google-chrome-stable`, `chrome` on `PATH`, then Playwright's browsers under `PLAYWRIGHT_BROWSERS_PATH` and `/opt/pw-browsers` (newest Chromium, then the headless shell; never chromedriver). `browser_session(action='status')` shows what was found.

## How the model uses the browser

| Tool | Purpose |
| --- | --- |
| `browser_open` | Open an address in a tab and return its readable text with refs. |
| `browser_read` | Read the current page in a view, a section, an element or filtered lines (no network). |
| `browser_find` | Find text: section, nearest ref and offset for `browser_read`. |
| `browser_extract` | Elements by CSS selector as text, Markdown, HTML or attributes. |
| `browser_click` | Click a link, submit button, checkbox, option; in chrome also JavaScript buttons and points. |
| `browser_fill_form` | Fill a form (text, lists, checkboxes, radios, files) and optionally submit it. |
| `browser_act` | type, press, hover, scroll, select, check, uncheck, focus, clear. |
| `browser_wait` | Wait for an address, element, text, expression or network quiet (chrome), or seconds. |
| `browser_evaluate` | Run JavaScript in the page (chrome); JSON result and console lines. Needs the caller's approval. |
| `browser_screenshot` | PNG/JPEG of the viewport, full page or an element, or a PDF (chrome). |
| `browser_history` | back, forward (stored copy in http), reload (repeats POST), list. |
| `browser_tabs` | list, new, switch, close. |
| `browser_session` | list, status, configure (user agent, language, headers, engine, window, JavaScript, images, dialogs, proxy), clear, reset, close. |
| `browser_cookies` | list (names, attributes, length and fingerprint, never values), set, delete, clear. Needs the caller's approval. |
| `browser_search` | DuckDuckGo, DuckDuckGo Lite, Bing, both (`all`) or a custom template; ads are counted, not shown. |
| `browser_request` | Any HTTP request: method, headers, query, JSON, form, multipart, raw or base64 body; `{{cookie:NAME}}` placeholders. Needs the caller's approval. |
| `browser_download` | Stream a file to disk by URL, by link/image ref, or the tab's current document. |

- **Refs.** Links, buttons, fields, forms, tables and media carry `[L5]`, `[B2]`, `[I3]`, `[F1]`, `[T1]`, `[M4]` in the text; tools take them as `ref` (`L5`, `[L5]` and `l5` are the same). In the http engine refs follow document order and stay stable for a history entry; in chrome they are stamped into the live page, grow monotonically and are never reused within a document. `N3` names a request of the network view.
- **Views.** `content` (main text, default), `page`, `text`, `html`, `outline`, `links`, `interactive`, `forms`, `tables`, `images`, `meta`, `structured`, `feed`, `headers`, `json`, `network`, `console`; navigation tools also accept `none`. `selector`/`ref` scope the DOM views, `section` picks an outline section, `filter` selects lines of list views, `json_path` selects JSON.
- **Pagination.** Every text-returning tool has a required `max_chars` (0 = everything) and `offset`; the result reports `next_offset` and `total_chars`. Nothing is cut silently: skipped ads, binary bodies not loaded, `max_bytes` cuts and shortened change lists are listed in `notes`. The length caps of semantic text (`SEMANTIC_*_LIMIT` in [`browser/semantic.py`](semantic.py)) are 0 by default, meaning no cut; a positive cap shortens the text and the change list says which caps applied.
- **Sessions.** A dialog has its own session (tabs, history, cookies, settings, stored responses `R#` and JavaScript results `E#`); MCP calls share `default`; `session` names one explicitly. Sessions live in memory until `reset`/`close` or process exit; `browser_session(action='clear')` frees stored copies.
- **Engines.** `http` (default) is fast and needs no JavaScript; `chrome` is a real headless Chrome per session with a fresh temporary profile; `auto` loads with http and switches the tab to chrome when the page is built by scripts or is a bot check, and stays on chrome for the same site. The switch and the reopening in chrome run only when the caller asks for `auto` or `chrome` (the `engine` argument, or a `default_engine` set in the session settings). An action only Chrome can do on an `auto` tab reopens the page in chrome and asks to repeat the action with the new ref; on an `http` tab it explains how to switch. Without Chrome the http page is kept and a note explains how to install Chrome. An unknown engine name is an error (phase `execution`), not a silent switch.
- **Typical chains.** `browser_search` → `browser_open(url)` → `browser_read(view='outline')` → `browser_read(section='2')`; `browser_open` → `browser_fill_form(fields=[…], submit=true)`; `browser_open` → `browser_click(ref='L5')` → `browser_download(ref='L7', path='downloads/', overwrite=false)`; `browser_request(url, json_body, json_path)` for APIs.
- **Tool lists.** A dialog with a saved list of tool categories offers the browser only when «Модуль: browser» is ticked.

## Security

- Every request, every redirect hop (http) and every request Chrome makes (Fetch interception on pages, frames and workers, with Chrome's own URL blocklist of literal link-local hosts as a backstop) passes the same link-local guard as `http_fetch`: cloud metadata and other link-local addresses are refused, and a resolver failure refuses the request. A child target whose interception cannot be enabled stays paused. WebSocket connections to such hosts are reported as warnings.
- A browser session never uses the owner's browser profile, cookies or logins: Chrome starts with a fresh `mkdtemp` profile, its own `HOME` and a minimal environment (no `DISPLAY`), and the profile is removed when Chrome closes; profiles left by a Logos process that died are swept on the first Chrome launch of the next one.
- Passwords, tokens, `Authorization` and cookie values are masked in everything the model sees (headers, forms, cookie lists show length and fingerprint only), registered with the execution trace for redaction, and never logged; trace arguments `headers`, `fields`, `value`, `body`, `json_body`, `form` and `proxy` are redacted.
- Downloads, screenshots and PDFs are written atomically through a hidden temporary file; paths follow the filesystem tools' rule.
- While Chrome runs, its DevTools port listens on 127.0.0.1 only.
- **TLS.** Certificate checking is on. `ignore_tls_errors` is an owner setting of `SessionSettings` ([`browser/settings.py`](settings.py), default off): the tool schema and `browser_session(action='configure')` do not accept it, so the model cannot pass or change it.
- **Approval.** `browser_request`, `browser_cookies` and `browser_evaluate` are declared with `requires_approval`; `browser_wait` needs the same approval when it is given an `expression`. A call without the caller's approval fails with phase `approval_required` and a text that says what is needed; no token is invented in the tool. The approval round-trip with the user interface is not finished in the repository.
