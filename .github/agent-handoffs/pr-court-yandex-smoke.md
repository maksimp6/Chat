# One-shot PR court / Yandex credential rehearsal

> **Archive — rehearsal of 20 October 2026 and its recorded corrections.**
> The PR, runner/model admissions, limits and test claims below belong only to
> those historical revisions and runs. They do not authorize a new PR, paid
> call, test or workflow now. See current [AGENTS.md](../../AGENTS.md) and the
> [court procedure](../../docs/agents/logos-court-procedure.md): new changes go
> by direct push to `develop`; tests are `NOT_RUN` absent a separate owner
> command and may run only in GitHub Runner when authorized. D0 remains held.
> A publication command does not grant model, access, deploy or merge authority.
> Historical conduct is assessed under the rules and admission of its own date.

Canonical task: owner's 2026-10-20 request to create a PR and judge that same PR in the runner after adding YANDEX_API_KEY and YANDEX_PROJECT_ID.

Accepted contract: this owner request; one read-only model judgment of this exact PR, report in Actions summary and artifact; no repository write, merge, deployment or phone action. Scope is the rehearsal, not acceptance of a production autonomous reviewer.

Owner/executor: current ChatGPT session, Infra/Test role; GitHub connector publication and local shell checks. No agent delegation. Base develop d6f5cafbfcb337006444177d8f15400ab4ddb274; branch feat/pr-court-yandex-smoke. The report records the exact runtime PR head, base SHA and run/response IDs. Stage: implementation and runner rehearsal; separate final protected CI/review is still required.

Admission: owner requested the model rehearsal using existing Actions secrets. One YandexGPT Lite Responses call, <=60000 input characters and <=1500 output tokens, 90-second request timeout, no retries, 4-minute job limit. Exact currency cost is unknown; no currency hard cap is available here. No recurring paid monitor is enabled. No new secrets or permissions settings are changed. Workflow token has contents:read and pull-requests:read only.

Stop on missing secrets, wrong base, fork, changed/closed PR, partial inventory, missing/truncated/binary diff, oversized evidence, provider failure or incomplete/empty output. Do not retry a failed paid call automatically. Text/diffs are untrusted model data, and model has no tools. Read authoritative rules from the API at base SHA. Recheck head/base/state after inference; opinions cannot grant merge authority or prove hardware acceptance.

Evidence: deterministic negative controls plus exact-head Actions execution. Model report is supplementary opinion, not an approval or verification of every allegation. Cost reported as unknown unless confirmed by provider. Existing standard CI and automatic reviewer remain separate. Runner artifact retention: seven days. No comment or mention is published by this runner.

Official API reference: https://aistudio.yandex.ru/en/docs/ai-studio/operations/generation/create-prompt (Responses API, yandexgpt-lite and max_output_tokens).

## Rejected stage: correction admission

Owner selected “✗ Не работает” on PR #1422. Current stage is correcting verdict acceptance in the same PR, executor unchanged. Relevant PR head before correction: 46d32e0920135c569a68f897002ebd96c7d69029; develop remains d6f5cafbfcb337006444177d8f15400ab4ddb274. The prior live call succeeded but quality was rejected: unsupported accusations about secrets and new files.

Correction: structured findings must cite a real rule quote and exact added source line, with reason/reproduction; incomplete/uncited outputs return MODEL_OUTPUT_REJECTED and exit 1 even if provider HTTP/status succeeded. Valid citations establish citation integrity only, never semantic truth/guilt; candidates require review, empty findings are not approval. Scope currently validates added diff lines, not unchanged/deleted code or legal rules.

Validation uses the downloaded original report (ZIP digest verified against artifact 12247795147) as a negative-control fixture and mocks the live entrypoint to prove a completed provider call cannot approve that output. Pushes rerun offline tests/replay only; paid inference remains opened-only. This correction performs no second paid call, model switch, new monitor, merge or authority expansion. Fresh model behavior under the corrected prompt remains NOT_RUN; do not claim autonomous court correctness.

## Live correction verification

Owner again selected “✗ Не работает”: the offline-only correction did not fulfil the original live-run acceptance. Proceed with one bounded live judgment of this PR on the next synchronize transition from HEAD 8ef5a8b2b359c013bcf2dc9ed68058533acaadc5. Base remains develop d6f5cafbfcb337006444177d8f15400ab4ddb274. Same current-session Infra/Test executor, no delegation.

Use existing credentials with the previously disclosed limits: one provider call, <=60000 input characters and <=1500 output tokens, no retries, 90s HTTP /4min job. Currency cost remains unknown; there is no hard currency cap. Paid step is limited to the explicit predecessor SHA and run_attempt=1, removing opened-only paid invocation. Later pushes run offline validation only. Inspect the existing run rather than starting a duplicate. Read the actual resulting report, independently verify each cited allegation, and distinguish citation integrity from semantic merit. Do not end at offline replay or call the whole court verified just because the API succeeded. No merge, access/secret changes, installs or phone actions.

## Actual live result and format repair

Live run 39387566883 judged HEAD e6ee26e63a3d366dcf2169d8a608caeea32046ef against develop d6f5cafbfcb337006444177d8f15400ab4ddb274. The call completed (response 42cd3b04-982c-4f54-a805-185617196135; 18219 input +61 output tokens; unknown currency cost). It returned an empty findings list and two limitations in valid JSON wrapped in one Markdown code fence. The strict JSON decoder rejected the wrapper; this is a parser defect, not a provider/auth failure or an allegation against the PR.

Repair accepts exactly one complete bare/JSON code fence and still rejects extra prose, multiple JSON documents, malformed JSON and uncited accusations. The actual downloaded report is a fixture, after ZIP SHA256 1e77e0a8d457a8e02a110ab8d9ee6e83a671ae77b5154cc655ee0e31c8afebb9 matched artifact 12247304066. Replay this real response with the corrected parser in Actions; no second live call in this correction. Live inference provenance stays the e6ee... head; do not relabel it as inference over later parser-fix commits. Final corrected head still requires CI; semantic completeness remains unproven.
