# Logos Pro live UI audit: Redmi 9, 2026-10-16

**Status: PARTIAL / BLOCKED, not VERIFIED.** This is a hardware screenshot-based exploratory UI audit, not a successful full E2E test.

## Environment and scope

- Device: **Redmi 9** (not 25062RN2DY), Android display rotated to landscape, screenshot **2340×1080**.
- App: real **Yandex Browser** at local **127.0.0.1:5000** on the device; UI controlled via the existing authorized RDC `root-shell`, `/system/bin/input` and `screencap`.
- GitHub reference: `develop` SHA `1ded6c83b4e122c4a33bc766964de4d4e5d96331`. **Deployed frontend SHA was not established.**
- Device-local original screenshots and SHA manifest: `~/logos-ui-audit-20261016/`. Public GitHub screenshots below have been reviewed/redacted.
- No saved application settings, no APK installation, no server restart, no deployment, no merge. UI taps and browser refreshes only. No model request was made.

## Screenshots (safe for public repository)

| Evidence | Shows | SHA-256 |
|---|---|---|
| [Gallery](evidence_gallery.jpg) | 12 labeled screen states including live browser outage | `8c98fe57c947f7857e31c7835b3c78e3ebc130b2288c4ce19f424e2824bccd4d` |
| [System status](evidence_status.png) | `message: undefined`, `metadata: undefined` | `475aade24c556dd3ad407d16dfcc31d7927e3fb95e0374f25c831b6e0f651b2e` |
| [File manager](evidence_files.png) | Vector Stores internal configuration error | `9c6b0c732e2028126a63067b076faba762227cfc63500116acef6d08be435646` |
| [Outage](evidence_outage.png) | Browser `ERR_CONNECTION_REFUSED` on localhost | `4185415c50dadf398a556a08bc135d328357dd2472c14f662f3da0fd9f75910d` |
| [Credential status redacted](evidence_credentials_redacted.png) | Cached `Подключён • авторизация OK` with past expiry | `a14c68d883f94d2bfeaa8f4922f4ea53689c400357024768070a0449458b53fb` |
| [Keyboard overlap redacted](evidence_keyboard_redacted.png) | On-screen keyboard overlays bottom of provider form | `bd9767c611b15563b04a723395039ee789409ec31fab3d66acbc382be2a9213b` |

**Important:** redactions conceal credential fields and fingerprint text. Never upload unredacted secret/account screenshots to public GitHub.

## Windows genuinely opened and observed

| UI surface | Evidence | Observation |
|---|---|---|
| Chat home / header | gallery #1 | Visible composer and send button; message round-trip **not tested** |
| Sidebar | gallery #2 | Opens, `Новый чат` present; no write action performed |
| System status | gallery #3 + status | Broken placeholders `undefined` exposed to users |
| Model selection | gallery #4 | List renders; selecting and calling a model not tested |
| SSH Runtime | gallery #5 | Layout opens; `authenticated owner identity is required` (access gate, not necessarily a UI bug) |
| Environments | gallery #6 | Empty state displays; English headings on Russian UI |
| MCP Servers | gallery #7 | Empty state and add-server affordance display; no server added |
| LLM/Responses settings | gallery #8 | Fields and toggles visible; bottom/keyboard and saving not tested |
| File manager | gallery #9 + files | Rendered modal, backend credential error for Vector Stores |
| Treasury | gallery #10 | Opened demo-top-up pane; soft keyboard covers much of content |
| Web-only updater | gallery #11 | Expected Android-only-update notice |
| Provider credentials | redacted status/keyboard | Expired Yandex credential still marked connected; input keyboard overlap |
| Browser when service fails | gallery #12 + outage | Local web application became inaccessible during exploratory check |

The first capture run used positional taps; its original filenames are **not** actual window names (header icons are horizontally scrollable). This report labels each captured surface by what was **visually** displayed.

## Findings and severity

1. **P0 / availability: application inaccessible during audit.** Browser displayed `ERR_CONNECTION_REFUSED` for `127.0.0.1:5000` at approximately 18:33 device time. Separate TCP socket test returned `ConnectionRefusedError`. **Root cause not established:** do not assume browser refresh triggered server crash. Existing remote control remained online; we did not restart the backend.
2. **P1 / frontend correctness: raw `undefined` fields in System Status.** Use localized meaningful fallback and populated stable data contract. See [status screenshot](evidence_status.png).
3. **P1 / credential state correctness: expired Yandex credential appears connected.** Existing issue **#1296**. Status reported cached success despite expired credential; screenshot redacted.
4. **P1 / file manager: raw internal environment variable in Vector Stores error.** `LOGOS_PROVIDER_CREDENTIAL_KEY is required for provider credentials` leaks implementation detail and gives no safe recovery UI. Display actionable local diagnostics and graceful degradation, never expose credentials.
5. **P1/P2 / responsive: provider credential modal covered by IME in landscape.** Ensure focused field and save/status areas can be scrolled into view above on-screen keyboard; test narrow landscape.
6. **P2 / discoverability/accessibility: very long icon-only header.** Some actions lie beyond horizontal scroll; labels/tooltips and focus/scroll affordance need verification. Several modal close affordances are small.
7. **P2 / visual consistency and localization:** English `Environments`, `SSH Runtime`, raw authorization message, `Vector Stores` in otherwise Russian interface; default-looking buttons and mixed modal styles; floating gear may overlap large modals.

## Not tested (no claim of passing)

Dedicated local Tools, Dozzle Logs, Project Tree, Departments, Memory, theme cycle, hidden rightmost header buttons, portrait layout, full-screen keyboard accessibility, file upload, model round-trip, dialogs requiring authorization, and any state-changing controls. The app went down before the remaining traversal. No replay after outage; do not claim complete coverage.

## Required next checks

- Diagnose lost localhost listener without restarting the only RDC channel. Restore backend **only with separately authorized safe runbook**.
- Re-run UI matrix with distinct stable semantics for each icon (not blind coordinate guesses), screenshot at portrait and landscape, test IME visibility, scrolling, close buttons, browser back/focus.
- Fix status serialization and provider expiration representation; validate file-manager error UX; remove English fallback and dangerous display of internal configuration details.
- Confirm frontend exact source SHA/version on the running device; do not infer equality from GitHub `develop`.
- Save successful screenshot/trace evidence for every tested state and E2E model response before declaring VERIFIED.

**Audit does not authorize merge or production changes.**
