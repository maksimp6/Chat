# Browser tool-call video evidence contract

This specification adds tool-use scenarios to the existing Logos browser screen-recording run. It does not claim the scenarios have run.

## Recording requirements

- Record the **actual browser viewport** throughout each scenario, not a synthetic HTML mock.
- Capture event timestamps (UTC), run ID, browser/runtime revision, installed backend revision, and tool-call correlation IDs.
- Record a screen video plus before/after screenshots for every scenario. Preserve browser console errors, network request status and server execution trace as separate machine-readable evidence.
- Redact API keys, credentials, personal files, cookies, authorization headers and private messages from all exported artifacts. Use isolated disposable fixtures and a test provider or stub with explicit labeling. Do not spend provider credits by default.
- Include a manifest with file names, byte sizes, SHA-256 hashes, scenario IDs, start/end timestamps, and PASS/FAIL/ERROR/NOT_RUN. Video presence alone never means PASS.
- Preserve the existing recording workflow; do not introduce a second competing capture job.

## Browser scenarios

| ID | Action visible on video | Required proof |
| --- | --- | --- |
| TOOL-01 | Open chat, request a safe read-only tool, inspect tool activity | One matching invocation and correlated trace; rendered tool result, no `undefined` |
| TOOL-02 | Expand and collapse the tool-call/result details | No lost message, layout overflow or console exception |
| TOOL-03 | Send a message requiring two sequential safe read-only tools | Both tool calls shown in correct order, with distinct IDs and results |
| TOOL-04 | Return a tool error from an isolated fixture | Visible failure state and trace error; no fabricated successful answer |
| TOOL-05 | Simulate a slow tool response | Loading indicator remains consistent; completion/error is reflected without duplicate message |
| TOOL-06 | Reload browser after completed tool call | Conversation and result remain present, with no duplicate invocation |
| TOOL-07 | Open a second conversation after a tool result | No leakage of tool output into the other conversation |
| TOOL-08 | Attempt a write-capable tool in a disposable fixture | Explicitly log whether an approval gate exists; never write to real user data |
| TOOL-09 | Cancel or stop a slow fixture tool | Record whether server execution actually stops; UI-only cancellation is FAIL for end-to-end cancellation |
| TOOL-10 | Repeat/retry after a network interruption | Show whether tool side effects duplicate; use a fixture counter, not real external actions |

## Acceptance

Each scenario must be captured in the browser and cross-checked against correlated backend traces and the manifest. Report screenshot/video time ranges and direct artifact paths. Mark any unexecuted scenario NOT_RUN. Do not mark VERIFIED from HTTP 200, green CI, or video capture alone. No actual tool execution or device changes are authorized by this document.
