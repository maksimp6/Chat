# Automatic Redmi 9 UI screenshots

Run the existing **Redmi 9 application server** workflow with operation
`screenshots` on the repository branch that should receive the evidence.
One runner step wakes Redmi 9, dismisses its existing non-secure keyguard,
opens `http://127.0.0.1:8080/` in the installed Iceraven browser, and captures
native Android PNGs after checking the visible UI state.

Every launch uses a unique query marker and requires a new navigation timestamp
and the server-rendered boot revision. If Android returns a reused tab without
navigating, one explicit browser navigation opens the fresh local URL. An old
or modified document is not accepted as the current application. The script
records an initial native frame before attempting the named transitions.

The named matrix contains ten frames: sidebar open, closed, reopened; model
picker; system status; and the five settings tabs (generation, output, routing,
advanced, theme). It uses the original buttons without sending a model request,
selecting a different model, or saving settings. Temporary panels are closed
before the creation scenario starts. Its final selection and sidebar state are
recorded as observed after reload, without a corrective sidebar toggle.

A second segment records the real creation transition on video and in
`events.json`: before action, opening the model picker, choosing the currently
selected model, the conversation creation request, selection in the sidebar,
the empty conversation view, and reload persistence. Additional native PNGs
show the checkpoints. A fetch observer forwards the creation and read requests,
recording same-origin method/path/status/timing and sanitized response IDs and
counts. It does not save message text, headers, or response bodies. A narrow
guard rejects `/api/chat` before transmission and reports an attempted
generation as a scenario failure. The timeline records wall-clock and monotonic times, while native
`screenrecord --bugreport` adds a timestamp to the video. The creation/reload
observer blocks model requests only during those checks.
After reload, the ordinary scenario sends one request through the real Send
button: «Разрешение уже есть, ничего не уточняй: сразу выполни поиск File
Search (RAG) по запросам «Логос», «Судный Логос» и «КРОВЬ, ВОПИЮЩАЯ ОТ ЗЕМЛИ» и
прочитай найденные диалоги с Логосом. Потом ответь на вопрос: можешь ли ты
судить как Логос?» The dialogues come from the chat RAG store,
which `redmi9-rag-ingest` fills with issues, pull requests, run metadata and the
`exports/` dialog archives (GigaChat exports and the Logos memory module).

The request and reply must persist without trace errors. A separate
`logos_rag_judgement` checkpoint records whether the request offered
`file_search`, whether the provider ran a `file_search_call`, and whether the
reply has text; it does not grade the judgement itself. Native PNGs
`22-logos-rag-top.png` and `23-logos-rag-bottom.png` show the start and end
after actual scrolling. The conversation video can run for 180 seconds; the
reply wait reserves time for screenshots and finalization. The newly created
conversation is preserved. The `tools_self_check` mode retains its separate
tool question and does not send the Logos RAG prompt.

The checks compare conversation IDs across the sidebar, current application
state, URL, and saved selection; count creation requests; check the empty view;
and detect sidebar/ARIA disagreement before any cleanup can hide it. A bounded
DOM observer records intermediate selection and message-count changes. Application
failures remain in `creation_scenario.failures`. Evidence is published before
the workflow reports an application failure. Video and timeline are required
for a successful new capture run. A PNG series cannot substitute for a missing
video without the run reporting that limitation.

Free-form settings fields and system-status message text receive temporary,
opaque screenshot masks. The script does not read or log those field values.
Masks are recorded in `manifest.json` and removed after each capture. Masked
text is not evidence that its underlying value is correct.

The manifest records the initial and ready Android screen states, actual app
and browser revisions, each requested surface, dimensions, byte sizes, and
SHA-256. PNGs and the MP4 are committed as Git LFS pointers under
`docs/screenshots/logos/<date>/run-<id>-<attempt>/`; the manifest and event timeline
are ordinary Git.
The publisher writes only to the real workflow branch, rejects `master`, and
uses a normal fast-forward push. It downloads the exact LFS objects again on
the Android runner and verifies bytes and hashes. `verify_screenshots` repeats
the remote verification without touching the screen.

The runner stops if the device is not Redmi 9, a secure unlock is required,
Iceraven or DevTools is unavailable, or a safe application view cannot be
verified. Failed UI transitions are captured and recorded in `matrix_scenario`
while the observed application view remains safe; they cannot produce an
overall successful verdict. It does not install software, disable a lock, restart
RDC, or claim coverage of every possible application state. The local server
and the existing browser DevTools setting must already be available.
