# Журнал изменений

История изменений проекта в формате: версия, дата, описание изменений, новые функции, исправления и критические изменения.

Для общего понимания структуры проекта и его компонентов ознакомьтесь с [README](README.md) и [архитектурой системы](architecture/overview.md).
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
