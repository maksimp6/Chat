# Уроки, которые не требуют второго каталога

Источники H01–H18 и ограничения полноты находятся в [индексе](README.md).
Это инженерные процедуры и исторические выводы; правовые заключения,
финансовые результаты и готовность production из них не следуют.

<a id="architecture"></a>
## Границы продукта и исполнения

H02: Conversation описывает пользовательский разговор; Agent — роль и поведение;
Model/Provider — вычислительный backend; Tools/MCP — доступные действия;
Memory — данные и их жизненный цикл. Чат остаётся интерфейсом к этим слоям.
Перед новой интеграцией нарисуйте путь конкретного пользовательского действия
до существующего service, найдите владельца identity/persistence и только затем
выбирайте adapter. Повторный business service в MCP создаёт второй источник истины.

Существующие [AI pipeline](../architecture/ai-execution-pipeline.md),
[Universal Tool Platform](../mcp/tools.md) и
`../../.agents/skills/logos-runtime-debugging/SKILL.md`
остаются каноническими. Usage/cost считает детерминированный код; текст модели
может объяснять результат, но не становится расчётным источником.

H05: контекст запроса создаётся один раз; его IDs проходят через provider,
tool boundary, continuation, persistence и ответ. Исторический `/api/chat`
терял явный `session_id`; отдельный тест искал `invocation_id` на корне Trace
вместо `trace.context`. Успех — согласованный контракт, а не любое зелёное
утверждение. Проверяйте isolation двух запросов и compatibility fallback.

## UI-настройка не равна provider-настройке

H04: сохранение работало, но request assembler читал `active_tool_categories`
без нужного преобразования `tools_config` и `selected_skills`. Повторная смена
хранилища не устраняла ошибку. Сегодня проверять надо актуальные
[settings modules](../../static/settings/settings_params.js),
[chat boundary](../../chat/mcp_routes.py) и
[request shaping](../../yandex_client_modules/request_mixin.py), а не переносить
исторический диагноз без сверки. Новая процедура:
[settings-payload-verification](../../.agents/skills/settings-payload-verification/SKILL.md).

## Checks, принятие и результат — отдельные доказательства

H18: перед объявлением check обязательным проверьте workflow events, branch/path
filters, условия jobs и фактическое имя завершившегося status на каждой base,
включая stacked PR. Агрегатор должен различать разрешённый нерелевантный skip
и пропущенную обязательную проверку. Для merge queue отдельно нужен
`merge_group`. После promotion проверьте, что integration branch существует.

Разделяйте код workflow, активацию ruleset, merge и deployment. Исторический
запрос review или отказ из-за квоты не является проведённым review. Ручная
проверка автора не становится независимым одобрением. Процедуры уже есть в
[github-ci-diagnosis](../../.agents/skills/github-ci-diagnosis/SKILL.md) и
[github-pr-readiness](../../.agents/skills/github-pr-readiness/SKILL.md).

H18/#1102: dependency diff review и полный аудит установленных пакетов решают
разные задачи. Изменения manifest/lockfile должны выбирать нужные test suites.
Проверяйте severity, scopes и отсутствие scope на clean/added/updated/removed
fixtures. Machine-readable failure, summary и annotation остаются результатом,
даже если публикация комментария получает HTTP 403. Проверка фактического pinned
action bundle на контролируемых API responses сильнее проверки YAML-строк,
но всё ещё симуляция, а не реальный fork/Dependabot token experiment.
Не расширяйте права только ради комментария. [#1102](https://github.com/maksimp6/Chat/pull/1102)
сообщает 64 focused tests и 23 simulation scenarios; здесь они не повторялись.

Аналогичный trust-boundary урок из [#496](https://github.com/maksimp6/Chat/issues/496#issuecomment-6106482490):
pinned manifest и точные test identities внутри PR-editable workflow не защищают
от удаления самого вызова verifier. Успех fast contracts #1106 — доказательство
конкретного запуска, не независимое native admission. Такая защита требует
отдельного trusted producer, exact-head binding и negative bypass fixture;
этот документационный PR не реализует и не включает её.

## Проектная схема должна быть связана с доказательствами

H13: KiCad-архив содержит native inputs, netlist, ERC, PDF/SVG и manifest.
Источник сообщает 17 passed с KiCad; без него 13 passed и четыре явных skip,
ERC — ноль errors/warnings/exclusions. Это учебная схема с идеальным RC расчётом.
Наличие библиотеки ngspice не доказало наличие работающего simulator и
подходящей NE555P модели. Подмена другим таймером не закрывала проверку.
Процедура: [source-bound-circuit-verification](../../.agents/skills/source-bound-circuit-verification/SKILL.md).

H14: manifold mesh из 296 triangles оказался недостаточно узнаваемым по
реальному render; disconnected shells и nonmanifold edge остались blockers.
«Watertight» нельзя переводить в «похоже, прочно и подходит». Для замены детали
нужны измеренные сопряжения и допуски. Официальная загрузка ассета не доказывает
права на коммерческую перепубликацию производных файлов.

H15: имя PETG profile не гарантировало effective material settings — результат
мог содержать PLA. Недостаток памяти worker вызывал перезапуски во время
подготовки slicer. Эквивалентные по проверенным параметрам 3MF имели разные
байты: фиксируйте hash каждого выхода и семантические инварианты отдельно.
Readback после upload подтверждает байты в storage, не начало печати.
H16: наблюдение отдельного job в FINISH 100% не доказывает прохождение всего
model → slicer → controller pipeline и не подтверждает посадку остывшей детали.

## Проверять последний переход до пользователя

H17: BrowserShim годится для детерминированного DOM-контракта; screenshot,
рендеринг и authenticated session требуют реального browser capability.
Для устройства отдельно проверяются команда, свежий кадр, input event и
наблюдаемое изменение UI. Сохранённый в worker файл ещё не является доставленным
пользователю вложением. Проверьте destination и открытие артефакта; не стройте
новый control service, пока не установлено, почему доступный путь не подходит.
Это критерии будущей приёмки, а не утверждение о выполненной здесь доставке
или управлении устройствами.
