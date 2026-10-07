# Безопасность системы

Безопасность Alice Pro строится на нескольких независимых границах. Наличие одной проверки не считается доказательством безопасности всей цепочки.

## Основные границы

- **Authentication** — short-token, GitHub/OAuth и MCP authentication применяются на соответствующих внешних поверхностях.
- **Authorization** — owner/runtime isolation и tool capability/approval проверяются до consequential execution.
- **Tool execution** — UniversalToolExecutor остаётся общей approval/policy boundary; транспорт не должен обходить её.
- **Secrets** — canonical Secret Store #755 хранит/разрешает значения вне model context; durable state содержит только refs/version metadata.
- **Durable state** — #776 отвечает за application/agent state, но не за secret plaintext.
- **Tracing** — ExecutionTrace должен сохранять проверяемое execution evidence без credential/token payloads.
- **Deployment** — зелёный CI и локальный smoke не заменяют live post-deploy acceptance.

## Fail-closed правило

Если ownership, scope, provider state, secret resolution или required acceptance evidence нельзя доказать, результат должен быть FAIL/UNKNOWN/BLOCKED, а не синтетический PASS.

`SKIPPED` допустим для необязательной диагностики, но не является доказательством обязательного security gate.

## Секреты

Secret values запрещены в:

- репозитории и обычном config;
- prompts/model context;
- ordinary MCP/tool results;
- durable Memory DB/legacy SQL как credential-value store;
- логах и ExecutionTrace.

Подробности: [Cloud.ru Secret Management](cloudru-secret-management.md) и [platform secret references](../platform/secrets.md).

## CI и standards

Repository security checks, CodeQL и другие статические/детерминированные проверки являются обязательным слоем, но не доказывают runtime production behavior. ASVS/WCAG/security ratchets нельзя ослаблять ради прохождения отдельного PR.

## Production acceptance

Для внешней поверхности доказательство должно соответствовать заявлению: exact deployed revision, реальный auth path, требуемый tool/API/browser scenario и отсутствие secret leakage. Локальный unit test подтверждает только локальный контракт.

## Ownership

Canonical work остаётся в соответствующих issues:

- #755 — Secret Store;
- #776 — durable state;
- #783 — platform convergence;
- #326 — production MCP/auth interoperability;
- #496 — fail-closed merge readiness.

Новые security документы не должны создавать конкурирующую архитектуру этим boundaries.
