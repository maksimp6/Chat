# Рабочий процесс разработки Alice Pro

Этот документ фиксирует воспроизводимый цикл для изменений в Chat/Alice Pro.

## 1. Типовой цикл

```text
Issue
  ↓
рабочая ветка от master
  ↓
небольшое изменение
  ↓
локальная проверка
  ↓
PR
  ↓
GitHub Actions / CI
  ↓
review
  ↓
merge в master
  ↓
проверка результата
```

Рекомендуемый формат ветки: `issue-<номер>-<краткое-имя>`.

Одна ветка и один PR должны решать одну независимую часть задачи. Крупную issue следует разбивать на последовательные PR, чтобы каждый шаг можно было отдельно проверить и откатить.

## 2. Совместная работа человека и AI-разработчика

Работа над Alice Pro организуется как сотрудничество владельца продукта и AI-разработчика.

### Роли

**Пользователь является владельцем продукта:**
- определяет цели и приоритеты;
- описывает требуемое поведение продукта;
- принимает решения о существенных архитектурных и продуктовых изменениях;
- подтверждает рискованные или потенциально необратимые действия.

**AI-разработчик отвечает за технический цикл:**
- анализирует репозиторий и существующие решения;
- предлагает декомпозицию и критерии приемки;
- реализует изменения в рамках согласованной задачи;
- добавляет и запускает тесты;
- создаёт PR, разбирает CI и исправляет найденные проблемы;
- проверяет результат после merge и обновляет Issue.

### Правило начала работы

Реализация не начинается, пока не определён проверяемый результат. Формулировка «починить трейс» должна быть заменена конкретным критерием, например:

> После выполнения двух tool calls Trace содержит два корректных вызова с аргументами, результатами и статусами, а пользовательский ответ не теряется.

## 3. Работа спринтами

Работа ведётся короткими спринтами длительностью примерно **3–7 дней**.

Для каждого спринта формулируется один **Sprint Goal**: проверяемый результат, который должен быть достигнут к концу итерации. Количество закрытых Issues само по себе не является целью.

Принципы спринта:

- спринт должен приносить работающий инкремент;
- крупные задачи разделяются на небольшие вертикальные инкременты;
- каждый инкремент должен быть проверяемым и по возможности оформляться отдельным PR;
- изменение цели спринта явно фиксируется в GitHub;
- срочные production-баги обрабатываются отдельным потоком и не маскируются под плановую работу.

### Definition of Ready

Задача готова к разработке, если у неё есть:

- понятная цель;
- описание ожидаемого поведения;
- критерии приемки;
- известные зависимости и блокеры;
- способ проверки результата.

### Definition of Done

Задача готова к завершению, если:

- код реализован;
- добавлены или обновлены тесты;
- документация обновлена, если это необходимо;
- локальные проверки пройдены;
- CI пройден;
- PR прошёл review;
- после merge выполнена проверка результата.

### Рабочий цикл спринта

```text
планирование
    ↓
разработка
    ↓
проверка / demo
    ↓
ретроспектива
    ↓
корректировка процесса
```

В конце спринта фиксируем три пункта:

1. Что сделано?
2. Что мешало?
3. Что следует изменить в следующем спринте?

### WIP-лимит

Для крупных задач действует правило **WIP = 1**: одновременно ведётся одна активная инженерная задача. Backlog может содержать много задач, но в разработке находится только одна основная задача.

Production-баги и блокеры могут временно обрабатываться отдельным потоком. После устранения блокера работа возвращается к основной задаче.

### Метрики без микроменеджмента

При необходимости отслеживаются:

- lead time;
- cycle time;
- размер PR;
- доля возвратов из CI;
- объём незавершённой работы.

Метрики используются для улучшения процесса, а не для формального контроля активности.

## 4. Перед изменением

Зафиксировать:
- какую проблему исправляем;
- ожидаемое поведение;
- минимальный набор файлов;
- регрессионный сценарий, который должен стать тестом.

Для изменений, связанных с Execution Trace или инструментами, дополнительно проверить, что техническая ошибка не превращается в ложный успешный результат.

## 5. Локальная проверка

Канонические formatter entrypoints:

```bash
bash scripts/format.sh write
bash scripts/format.sh check
```


Опциональный локальный pre-commit hook использует тот же canonical formatter и не создаёт отдельный набор правил:

```bash
bash scripts/install_git_hooks.sh
```

Hook выполняет только `bash scripts/format.sh check`. Если форматирование не проходит, он блокирует commit и показывает команду `bash scripts/format.sh write`. Существующий чужой `.git/hooks/pre-commit` installer не перезаписывает без явного `--force`. Полный pytest из hook не запускается; CI остаётся authoritative.

### Hot-file modularity report

Для поиска файлов с высокой исторической стоимостью изменений используется read-only анализатор:

```bash
python scripts/hot_file_metrics.py \
  --commits 200 \
  --top 20 \
  --json-out hot-file-metrics.json \
  --markdown-out hot-file-metrics.md
```

Команда читает Git history, поэтому для воспроизводимого результата нужна доступная история репозитория. В CI Application job делает checkout с `fetch-depth: 0`, запускает тот же анализ на окне 200 commits, печатает Markdown в log и GitHub Step Summary, а `hot-file-metrics.json` и `hot-file-metrics.md` входят в существующий artifact `coverage-<run_number>`.

Score прозрачно комбинирует touches, churn, churn/LOC, число authors и recency. Результат классифицируется как `split_candidate`, `extract_shared_logic` или `watch`. Это **сигнал для архитектурного review**, а не merge gate и не команда на автоматический refactor. Текущие exclusions заданы в коде анализатора и пока не настраиваются отдельными CLI allow/deny rules.

### Python AST repository map

Первый deterministic AI-index slice строится вручную:

```bash
python scripts/build_ai_index.py \
  --root . \
  --output ai-index.json
```

Без `--output` JSON печатается в stdout; `--compact` меняет только форматирование. В Git working tree индексируются только tracked `*.py` через `git ls-files`. Для каждого файла сохраняются path/module, SHA-256, test flag, symbols с line/end_line, imports и синтаксические call names; также формируются summary и эвристический `tests_by_module`.

Индекс не хранит исходный текст, env values или secrets и не содержит timestamps, поэтому при одинаковом дереве результат детерминирован. Граница текущего slice намеренно узкая: только Python AST. Call names не являются resolved cross-module call graph, `tests_by_module` не доказывает фактическое coverage, а JS/TS index, semantic resolution, routes/docs/issues/PR links, incremental cache, query API/tool и автоматическая agent integration пока не реализованы. #540 не добавляет отдельный CI artifact и не делает индекс обязательным gate.

Минимальный backend-набор:

```bash
python -m compileall -q .
python tests/validate_frontend_modules.py
python tests/validate_runtime_modules.py
pytest -q
```

Для быстрого цикла после изменения tool-loop или диагностики:

```bash
pytest -q tests/test_responses_tool_loop.py test_partial_output.py tests/test_tool_execution_contract.py
```

Для Android CI-эквивалент выглядит так:

```bash
cd android
python scripts/stage_python.py
gradle --no-daemon -PaliceBuildNumber=<run> -PaliceCommitHash=<sha> :app:testDebugUnitTest :app:assembleDebug
```

Основной GitHub Actions pipeline дополнительно выполняет:

- frontend JavaScript regression suite с V8 coverage и единым `frontend-test.log`;
- live Flask resource contract tests;
- полный Python suite с `--durations=30`, line/branch coverage и 100% diff coverage для изменённых Python-строк;
- полный PostgreSQL suite с `pytest --durations=30 -q`;
- PostgreSQL backup/restore verification;
- Android unit tests, debug APK build, verification и artifact upload;
- dependency cache для pip/npm/Gradle и timing telemetry в GitHub Step Summary.

Destructive PostgreSQL isolation в тестах разрешён только для disposable test databases. Для него одновременно обязательны:

- `ALICE_PYTEST_POSTGRES_RESET=1`;
- имя БД, заканчивающееся на `_test` или `_ci`.

`tests/postgres_test_guard.py` fail-closed отклоняет любой другой target. Никогда не направлять этот режим на production/staging database.

Локальная проверка должна ловить обычные ошибки как можно раньше, но обязательный CI на exact current PR head остаётся merge-gate.

## 6. Что должно быть в PR

PR должен содержать:
- связь с исходной issue;
- краткое описание изменения;
- проверенные команды;
- отмеченные ограничения или известные проблемы.

Не смешивать в одном PR:
- функциональный баг;
- крупный рефакторинг;
- изменение production-инфраструктуры;
- несвязанные UI-изменения.

Для #75 изменения вводятся небольшими PR. Первый этап стандартизирует сам процесс и регрессионные проверки.

## 7. Инструменты и Execution Trace

Для каждого локального или MCP-инструмента результат должен иметь однозначный статус:
- успешный результат не должен содержать скрытую ошибку;
- ошибка инструмента должна возвращаться вызывающему контуру;
- исключение должно попадать в ExecutionTrace.errors;
- вызов инструмента должен оставаться в ExecutionTrace.tool_calls с error и фактическим временем выполнения;
- пользовательский ответ не должен утверждать успех, если инструмент завершился ошибкой.

Для многошагового Responses API несколько обращений к API остаются шагами одной пользовательской операции и должны находиться в одном Execution Trace.

## 8. GitHub как единый источник истины

- **Issue** хранит цель, контекст, критерии приемки и оставшуюся работу.
- **PR** описывает конкретные изменения.
- **CI** подтверждает техническую проверку.
- **Комментарии** фиксируют решения и изменения направления.
- **Документация** хранит устойчивые правила проекта.
- **Execution Trace** служит техническим подтверждением поведения Alice Pro.

## 8.1 API-контракты

Любое взаимодействие через HTTP API выполняется по явному именованному
контракту. Источник истины для формы request/response не должен быть размазан
между route, frontend и тестами.

Для нового или изменённого endpoint обязательно:

- определить request, success-response и error-response contracts;
- запрещать неизвестные поля, если они явно не разрешены контрактом;
- валидировать типы, required/nullable поля и enum-значения;
- проверять реальные Flask request/response против тех же production-контрактов;
- иметь негативные тесты на missing/extra/wrong-type payloads;
- не обходить общий contract boundary прямым чтением/сериализацией JSON.

Подробные правила: [API Contracts](api/contracts.md).

## 8.2 Read-only merge readiness

После #516 и #523 в репозитории есть два read-only инструмента проверки готовности PR:

- CLI `scripts/merge_readiness.py`;
- GitHub Actions workflow `.github/workflows/merge-readiness.yml`.

Они собирают и оценивают состояние PR, но **не выполняют merge, не синхронизируют ветку и не меняют branch protection**.

### Ручной CLI

Для запуска нужен установленный и аутентифицированный `gh` с read-доступом к PR, checks и review threads.

Пример:

```bash
python scripts/merge_readiness.py \
  --repo maksimp6/Chat \
  --pr 123 \
  --base master \
  --required-check "Application tests" \
  --required-check "PostgreSQL integration" \
  --required-check "Android debug APK" \
  --pretty
```

Вместо повторяемых `--required-check` можно использовать переменную
`MERGE_REQUIRED_CHECKS` со списком точных имён check-run через запятую.

CLI печатает JSON с полями `ready`, `head_sha`, `behind_by` и `blockers`.
Код завершения:

- `0` — snapshot подтверждает `ready=true`;
- `1` — есть blocker или snapshot не удалось собрать.

Основные blocker codes:

- `pull_request_not_open`;
- `draft`;
- `wrong_base`;
- `snapshot_changed`;
- `behind_master`;
- `required_checks_unconfigured`;
- `required_check_missing`;
- `check_pending`;
- `check_failed`;
- `review_threads`;
- `review_threads_truncated`;
- `collection_error`.

Проверка fail-closed: без явной конфигурации required checks readiness не считается доказанной.

### GitHub Actions snapshot

Workflow `Merge readiness snapshot` запускается для non-draft PR в `master` на:

- `opened`, `synchronize`, `reopened`, `ready_for_review`;
- review `submitted` / `dismissed`;
- review-comment `created` / `edited` / `deleted`.

Он checkout'ит exact `pull_request.head.sha`, использует только read-permissions и проверяет:

- `Application tests`;
- `PostgreSQL integration`;
- `Android debug APK`;
- `Auto-format repository`;
- `Trivy repository scan`;
- `Zizmor GitHub Actions audit`;
- `CodeQL (python)`;
- `CodeQL (javascript-typescript)`.

Для missing/pending checks workflow ждёт не более `180 × 5` секунд, то есть 15 минут.
Результат JSON публикуется в job log и GitHub Step Summary.
При новом событии того же PR устаревший snapshot run отменяется.

### Ограничения snapshot

Snapshot относится только к состоянию, которое было собрано в конкретном run.

- обычное продвижение `master` само по себе не создаёт новый snapshot run для старого PR head;
- resolve/unresolve review thread сам по себе не является workflow trigger;
- snapshot не обновляет ветку и не заменяет strict up-to-date policy ruleset;
- snapshot сейчас информационный и не является самостоятельной заменой защищённого merge gate.

Поэтому непосредственно перед merge всё равно проверяем:

1. `behind master = 0`;
2. required checks зелёные на exact current PR head;
3. unresolved review threads отсутствуют;
4. merge выполняется только защищённым GitHub-путём.

## 9. Проверка перед merge

Одного зелёного CI недостаточно. Перед merge проверяем:

- соответствие изменения исходной Issue;
- отсутствие очевидных регрессий;
- корректность Execution Trace, UI и данных в БД, если они затронуты;
- наличие понятного rollback-пути;
- `behind master = 0`;
- required checks зелёные на exact current PR head;
- нет unresolved review threads;
- Copilot review используется автоматически и не триггерится вручную;
- `@codex review` запускается один раз на финальном ready-to-merge head.

После review-fix повторный Codex/Copilot review не требуется: новый head должен пройти свежий CI, а исправленные threads должны оставаться закрытыми.

## 10. Rollback

Для кода:
1. Найти merge-коммит проблемного PR.
2. Откатить его отдельным PR через `git revert`.
3. Повторно пройти CI и review.
4. После исправления исходной причины внести отдельный небольшой PR.

Не использовать `reset --hard` для переписывания `master`.

Для database migrations не откатывать схему вручную только ради возврата Git. Использовать соответствующий migration workflow и отдельную согласованную миграцию.

## 11. Проверка после merge

После merge проверить:
- последний CI run зелёный;
- изменённый сценарий воспроизводится в `master`;
- для Execution Trace сохранены request, responses, tool calls и errors;
- production-only изменения попали в предназначенный для них workflow.

## 12. Definition of Done для этапа

Изменение считается готовым, когда:
1. есть автоматический регрессионный тест для исправленного контракта;
2. локальные проверки проходят;
3. CI проходит;
4. PR содержит понятное описание и результаты проверки;
5. merge выполнен без скрытого незаписанного изменения;
6. при необходимости существует понятный путь rollback.
