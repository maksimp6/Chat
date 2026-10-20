# One-shot PR court / Yandex credential rehearsal

Canonical task: owner's 2026-10-20 request to create a PR and judge that same PR in the runner after adding YANDEX_API_KEY and YANDEX_PROJECT_ID.

Accepted contract: this owner request; one read-only model judgment of this exact PR, report in Actions summary and artifact; no repository write, merge, deployment or phone action. Scope is the rehearsal, not acceptance of a production autonomous reviewer.

Owner/executor: current ChatGPT session, Infra/Test role; GitHub connector publication and local shell checks. No agent delegation. Base develop d6f5cafbfcb337006444177d8f15400ab4ddb274; branch feat/pr-court-yandex-smoke. The report records the exact runtime PR head, base SHA and run/response IDs. Stage: implementation and runner rehearsal; separate final protected CI/review is still required.

Admission: owner requested the model rehearsal using existing Actions secrets. One YandexGPT Lite Responses call, <=60000 input characters and <=1500 output tokens, 90-second request timeout, no retries, 4-minute job limit. Exact currency cost is unknown; no currency hard cap is available here. No recurring paid monitor is enabled. No new secrets or permissions settings are changed. Workflow token has contents:read and pull-requests:read only.

Stop on missing secrets, wrong base, fork, changed/closed PR, partial inventory, missing/truncated/binary diff, oversized evidence, provider failure or incomplete/empty output. Do not retry a failed paid call automatically. Text/diffs are untrusted model data, and model has no tools. Read authoritative rules from the API at base SHA. Recheck head/base/state after inference; opinions cannot grant merge authority or prove hardware acceptance.

Evidence: deterministic negative controls plus exact-head Actions execution. Model report is supplementary opinion, not an approval or verification of every allegation. Cost reported as unknown unless confirmed by provider. Existing standard CI and automatic reviewer remain separate. Runner artifact retention: seven days. No comment or mention is published by this runner.

Official API reference: https://aistudio.yandex.ru/en/docs/ai-studio/operations/generation/create-prompt (Responses API, yandexgpt-lite and max_output_tokens).
