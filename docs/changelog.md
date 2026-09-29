# Журнал изменений

История изменений проекта в формате: версия, дата, описание изменений, новые функции, исправления и критические изменения.

Для общего понимания структуры проекта и его компонентов ознакомьтесь с [README](README.md) и [архитектурой системы](architecture/overview.md).


## 2026-09-29 — безопасность, CI, async runtime и Cloud.ru

### Безопасность и секреты

- Добавлен backend-only адаптер Cloud.ru Secret Management и усилены границы хранения секретов:
  [#483](https://github.com/maksimp6/Chat/pull/483).
- Удалено process-global удержание plaintext-секретов; lifecycle redaction registry очищается после finalization:
  [#493](https://github.com/maksimp6/Chat/pull/493).

### PostgreSQL и безопасность deployment

- Прикладной путь переведён на полноценную PostgreSQL-совместимость с CI-проверкой полного suite:
  [#484](https://github.com/maksimp6/Chat/pull/484).
- Добавлен fail-closed PostgreSQL preflight для опасных ролей/DSN:
  [#485](https://github.com/maksimp6/Chat/pull/485).
- CI теперь выполняет backup/restore verification схемы PostgreSQL; destructive test reset разрешён только для disposable `*_test` / `*_ci` БД при `ALICE_PYTEST_POSTGRES_RESET=1`.

### CI и review lifecycle

- Форматирование унифицировано через `bash scripts/format.sh write|check`, formatter versions закреплены в зависимостях:
  [#488](https://github.com/maksimp6/Chat/pull/488).
- Stale Format runs отменяются, чтобы не расходовать runner resources:
  [#489](https://github.com/maksimp6/Chat/pull/489).
- Добавлен fail-closed merge gate: `behind master = 0`, зелёные checks на exact current head и запрет stale-CI merge:
  [#497](https://github.com/maksimp6/Chat/pull/497).
- Добавлены cache Python/npm/Gradle и timing telemetry:
  [#499](https://github.com/maksimp6/Chat/pull/499).
- Убраны дублирующие test runs, frontend log сохраняется без второго прогона, PostgreSQL reset выполняется lazy и детерминированно:
  [#495](https://github.com/maksimp6/Chat/pull/495).
- Copilot review закреплён как автоматический; ручной `@copilot review` больше не используется, а Codex запускается один раз на финальном ready-to-merge head:
  [#504](https://github.com/maksimp6/Chat/pull/504).
- Добавлен read-only CLI `scripts/merge_readiness.py`, который fail-closed проверяет draft/base/head/behind/required checks/review threads и возвращает структурированный JSON без write-side effects:
  [#516](https://github.com/maksimp6/Chat/pull/516).
- Добавлен информационный GitHub Actions `Merge readiness snapshot` на exact PR head с read-only permissions и bounded polling missing/pending checks до 15 минут:
  [#523](https://github.com/maksimp6/Chat/pull/523).
  Snapshot не синхронизирует ветку, не заменяет strict up-to-date branch protection и не является самостоятельным required merge gate.

### Async/runtime validation

- Добавлен `pytest-asyncio` harness для параллельных invocation/trace contexts, cancellation и leaked-task checks:
  [#501](https://github.com/maksimp6/Chat/pull/501).
- Добавлена детерминированная проверка AgentGateway timeout isolation: timed-out slow worker не блокирует fast sibling и не превращается в ложный `completed`:
  [#515](https://github.com/maksimp6/Chat/pull/515).

### Cloud.ru и EDS

- Добавлен комплект EDS/Cloud.ru skill/runbook:
  [#480](https://github.com/maksimp6/Chat/pull/480).
- Container Apps client переведён на v2 read/list/PATCH contract, полную pagination и allowlist безопасных update fields:
  [#498](https://github.com/maksimp6/Chat/pull/498).

Наличие кода Container Apps v2 в `master` не означает подтверждённый live deployment. Production rollout и внешняя MCP-интеграция по-прежнему требуют отдельной проверки на реальной инфраструктуре.

## 2026-09-28 — изменения в репозитории

- Пакеты `invocation/` и `browser/` заменили соответствующие корневые модули:
  [#431](https://github.com/maksimp6/Chat/pull/431),
  [#432](https://github.com/maksimp6/Chat/pull/432).
- Удалены неиспользуемые agent loops и дублирующий helper-слой; CLI переведён
  на backend API, GitHub-agent сохраняет lifecycle invocation и финальный trace:
  [#434](https://github.com/maksimp6/Chat/pull/434),
  [#435](https://github.com/maksimp6/Chat/pull/435),
  [#436](https://github.com/maksimp6/Chat/pull/436),
  [#437](https://github.com/maksimp6/Chat/pull/437).
- Удалена интеграция Supabase; SQLite, PostgreSQL и локальное сохранение
  Execution Trace остаются:
  [#439](https://github.com/maksimp6/Chat/pull/439).
- Workflow Alice получил поддержку GitHub App с fallback на персональный
  токен при отсутствии настройки App:
  [#451](https://github.com/maksimp6/Chat/pull/451).
  Наличие кода не подтверждает установку App в аккаунте.
- Добавлен вход через GitHub, выключенный до настройки OAuth App:
  [#453](https://github.com/maksimp6/Chat/pull/453).
  Это отдельная интеграция от GitHub App, используемого workflow Alice.
- Уточнены инструкции установки, порт backend/CLI, источник ключей провайдера,
  Android toolchain и ссылки навигации.

Этот раздел фиксирует изменения кода и документации, а не подтверждённый
production-деплой. Переход на Cloud.ru отслеживается отдельно в
[#440](https://github.com/maksimp6/Chat/issues/440).

## Ранее

- 2026-09-24: force branch-master preview redeploy after provider credential runtime migration.
