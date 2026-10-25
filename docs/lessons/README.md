# Проверяемые уроки и проектные навыки

Срез: **2026-10-11**. Это каталог повторно применимых процедур из доступного
опыта работы над Logos Pro, а не архив переписки и не заявление о полноте истории.
Ранний подтверждённый материал начинается 2026-08-16; это не обязательно первый
разговор. Исторические результаты ниже отделены от повторных проверок кода.

## Как пользоваться

Выберите триггер в таблице, прочитайте соответствующий `SKILL.md`, подготовьте
его входы и заранее определите доказательство успеха. Процедуры не предоставляют
доступа к сервисам и не означают разрешение на deployment, изменение прав или
физические действия. Для текущего интерфейса используйте
[аудит frontend](../frontend/capability-gaps-2026-10-11.md).

Канонический каталог остаётся в [.agents/skills](../../.agents/skills/README.md).
Здесь нет второго формата навыков или личной установки. Десять новых пакетов
содержат проектные процедуры. Их формат проверяет существующий валидатор.
Существующий [role allowlist](../../agent_skills/registry.py) не изменён:
новые пакеты доступны для чтения и общего каталога, но **не подключены к
role-scoped выбору Logos**. Это отдельная будущая интеграция, не скрытая часть
документационного PR.

## Каталог без дублирования

У каждого нового пакета есть триггер, входы, процедура, проверки успеха,
типичные ошибки, ограничения и выход. Уже существующие навыки переиспользуются.

| Триггер | Каноническая процедура | Происхождение и граница |
| --- | --- | --- |
| Проверить идею AI-сервиса до автоматизации | [service-hypothesis-validation](../../.agents/skills/service-hypothesis-validation/SKILL.md) | H01; планирование, не доказательство дохода |
| Ошибка типов deferred promise или mock | [typed-async-contract-repair](../../.agents/skills/typed-async-contract-repair/SKILL.md) | H03; исторический успешный build, не текущий frontend Logos |
| Настройки сохранены, но модель их игнорирует | [settings-payload-verification](../../.agents/skills/settings-payload-verification/SKILL.md) | H04; проверять каждый переход UI → payload |
| MCP достижим, но tools/identity/scopes не работают | [mcp-authorization-diagnosis](../../.agents/skills/mcp-authorization-diagnosis/SKILL.md) | H06–H08; старые PR не выдавать за shipped |
| Нужно доказать durability, restore или cutover | [durable-storage-evidence](../../.agents/skills/durable-storage-evidence/SKILL.md) | H12; контракт отдельно от миграции |
| Конфиг, deployment и фактическое состояние расходятся | [deployment-evidence-review](../../.agents/skills/deployment-evidence-review/SKILL.md) | H09, H11; read-only проверка evidence, не deploy |
| Документы privacy расходятся с кодом | [compliance-evidence-reconciliation](../../.agents/skills/compliance-evidence-reconciliation/SKILL.md) | H10; инженерная проверка, не правовое заключение |
| Нужно архивировать проверяемую редактируемую схему | [source-bound-circuit-verification](../../.agents/skills/source-bound-circuit-verification/SKILL.md) | H13; ERC не заменяет SPICE и стенд |
| Надо передать модель/слайс/готовый файл без ложного успеха | [3d-artifact-handoff](../../.agents/skills/3d-artifact-handoff/SKILL.md) | H14–H16; стадии и реальные байты отдельно |
| Требуется найти недостающие взаимодействия UI | [frontend-capability-audit](../../.agents/skills/frontend-capability-audit/SKILL.md) | H02, H17 и текущий аудит; UI/backend/PR/deploy отдельно |
| Добавляются агенты, модели или tools | [Архитектурный урок](engineering-lessons.md#architecture) + [issue-to-pr](../../.agents/skills/issue-to-pr/SKILL.md) | H02; продолжать существующую архитектуру |
| Расходятся invocation/session/trace IDs | `../../.agents/skills/logos-runtime-debugging/SKILL.md` | H05; один request-scoped контекст |
| PR завис или старое зелёное CI не соответствует head | [github-pr-readiness](../../.agents/skills/github-pr-readiness/SKILL.md), [github-ci-diagnosis](../../.agents/skills/github-ci-diagnosis/SKILL.md) | H07, H18; точный head и события workflow |
| Нужна проверка release/deployment evidence | [release-readiness](../../.agents/skills/release-readiness/SKILL.md), [cloudru-change](../../.agents/skills/cloudru-change/SKILL.md) | H09, H11; новый навык дополняет проверкой происхождения артефакта |
| Утечки, неверные scopes или доверие к внешним данным | [security-review](../../.agents/skills/security-review/SKILL.md) | H08, H14; конкретный trust boundary |
| Устарели документация или статусы | [docs-sync](../../.agents/skills/docs-sync/SKILL.md) | Все строки; metadata и код важнее старой прозы |
| Нужен offline preflight или наблюдение печати | [PR #1109](https://github.com/maksimp6/Chat/pull/1109) | H16; существующие draft-пакеты `3d-print-preflight` и `a1-mini-print-watch` здесь не копируются |

## Источники и покрытие истории

Класс **H** — очищенный исторический материал; **R** — публичные репозиторные
артефакты/metadata; **L** — локальная проверка в этом аудите. Указанные в H/R
числа тестов — результаты источников, а не новая проверка оборудования.
Для исторических обсуждений без публичного артефакта сохраняется только общий
урок с датой и ограничением; приватные ссылки не публикуются.

| ID / дата | Проверяемое содержание | Источник и предел доказательства |
| --- | --- | --- |
| H01 / 2026-08-16 | Путь от AI-навыка к проверке потребности | Очищенное обсуждение; нет читаемого итогового артефакта, клиентов или результата бизнеса |
| H02 / 2026-09-05 | Conversation / Agent / Provider / Tools / Memory | Очищенное архитектурное обсуждение; проектное предложение |
| H03 / 2026-09-11 | Типизированные deferred promises | В историческом материале зафиксирован успешный `tsc` + Vite build; полный исходный проект здесь не восстановлен |
| H04 / 2026-09-13 | Разрыв между settings storage и provider payload | Исторические фрагменты кода; не доказательство, что все исправления уже landed |
| H05 / 2026-09-16 | InvocationContext, isolation и совместимость | [#46](https://github.com/maksimp6/Chat/pull/46), [#49](https://github.com/maksimp6/Chat/pull/49): оба merged, metadata перепроверена |
| H06 / 2026-09-23 | MCP как адаптер продуктовых services | [#233](https://github.com/maksimp6/Chat/pull/233): closed **без merge**, только историческая ссылка |
| H07 / 2026-09-25 | MCP protocol revision и preview owner | [#327](https://github.com/maksimp6/Chat/pull/327): closed **без merge**; детали диагностики частичны, runtime не перепроверен |
| H08 / 2026-10-04 | Сохранение scopes и отдельные auth-пути | [#781](https://github.com/maksimp6/Chat/pull/781) merged; [#783](https://github.com/maksimp6/Chat/issues/783) фиксирует последующие исправления, включая #819 |
| H09 / 2026-09-29 | Разные cloud credentials/lifecycle/logs | [#480](https://github.com/maksimp6/Chat/pull/480) merged; runbook/config kit, не live rollout |
| H10 / 2026-10-03 | Deterministic Compliance Linker | [#732](https://github.com/maksimp6/Chat/pull/732) closed без merge; [#729](https://github.com/maksimp6/Chat/issues/729) open, не shipped compliance gate |
| H11 / 2026-10-04 | Desired state, lanes, immutable promotion | [#781](https://github.com/maksimp6/Chat/pull/781), [#783](https://github.com/maksimp6/Chat/issues/783); foundation и containment не закрывают convergence/recovery |
| H12 / 2026-10-04–08 | Durable engine отдельно от миграции | [#776](https://github.com/maksimp6/Chat/issues/776), принятый [v1 contract](../memory/memory-db-v1-contract.md); новые engines остаются отдельными Draft PR |
| H13 / 2026-10-09 | Source-bound KiCad validation | [#1071](https://github.com/maksimp6/Chat/pull/1071), [pinned report](https://github.com/maksimp6/Chat/blob/5a5b5b9339c3fb963bccf41f04be4a392dc7e921/docs/artifacts/kicad/ne555-led-blinker/validation/TEST_REPORT.md); open draft, аппаратная проверка отсутствует |
| H14 / 2026-10-10 | Реальная геометрия, ingestion и immutable model edits | [#1093](https://github.com/maksimp6/Chat/issues/1093), [#1094](https://github.com/maksimp6/Chat/pull/1094), [#1097](https://github.com/maksimp6/Chat/issues/1097), [#1098](https://github.com/maksimp6/Chat/pull/1098); topology не доказывает сходство или посадку |
| H15 / 2026-10-10–11 | Effective slicer settings, readback, неодинаковые ZIP bytes | [#1096](https://github.com/maksimp6/Chat/issues/1096), [#1101](https://github.com/maksimp6/Chat/pull/1101), [slicer run](https://github.com/maksimp6/Chat/actions/runs/38112955571); не production adapter |
| H16 / 2026-10-11 | Bounded preflight и независимое наблюдение печати | [#1108](https://github.com/maksimp6/Chat/issues/1108), [#1109](https://github.com/maksimp6/Chat/pull/1109); физическое наблюдение не связано автоматически с модельным pipeline |
| H17 / 2026-10-09–11 | Browser capabilities и доставка файла | [#409](https://github.com/maksimp6/Chat/issues/409), [#650](https://github.com/maksimp6/Chat/issues/650); отдельные уроки доставки имеют более слабую историческую опору, без заявлений о воспроизведённых тестах |
| H18 / 2026-10-10–11 | Реальные checks до required rules, fail-closed dependency review | [#1084](https://github.com/maksimp6/Chat/issues/1084), [#1099](https://github.com/maksimp6/Chat/pull/1099), [#1100](https://github.com/maksimp6/Chat/pull/1100), [#1102](https://github.com/maksimp6/Chat/pull/1102); состояние уточнено ниже |
| L01 / 2026-10-11 | Код двух веток, offline smoke и Chromium error scenarios | [Frontend audit](../frontend/capability-gaps-2026-10-11.md); локальные синтетические данные, без live provider/device/deployment |

Покрытие ограничено доступными читаемыми материалами: часть сообщений усечена
или недоступна, вложения восстановлены не полностью, широкие подборки истории
не исчерпаны. Архивированные, удалённые и неиндексированные разговоры не
восстанавливаются по отсутствию результатов. Ранний материал H01–H04 остаётся
историческим свидетельством, а не воспроизводимым сегодняшним build.

## Устаревающие статусы на 2026-10-11

- `master`: `51aaf8273f4f465fe78aa0a79e76a22d8f141e35`.
- `develop`: `9d658587871007a041f1981c084a80d2de6d78bd`. Разница деревьев — только
  Dependency Review workflow и два тестовых файла, не приложение.
- #1084 закрыт: подготовка workflow выполнена через #1099 и promotion #1100.
  Это не незавершённая реализация прежнего blocker. `develop integration`
  остаётся Disabled; включение — отдельное решение.
- #1102 **merged в develop**, хотя тело сохраняет старое «Draft/no merge».
  Общий required gate Dependency Review ещё не включён. Реальное завершение
  check и его обязательность — разные факты.
- `master` PR-only включён и проверен 11 октября; это датированное состояние,
  не основание изменять правила из этой документации.
- #1071, #1098, #1101, #1109 — open drafts. У #1098 фактическая base — `master`,
  хотя часть описания говорит о `develop`.
- [#1112](https://github.com/maksimp6/Chat/issues/1112) координирует 3D-потоки;
  [Draft #1114](https://github.com/maksimp6/Chat/pull/1114), head
  `3e10c880a1b056ce7ee578960307ee0bd8b920e7`, каталогизирует 17 артефактов.
  `test_tube_holder.3mf` и модель из [#1110](https://github.com/maksimp6/Chat/pull/1110)
  не содержат G-code; archive CRC проверен локально. Каталог — не print pipeline.
- [#1106](https://github.com/maksimp6/Chat/pull/1106), head
  `a82916624704cf65455d15d73df12faa98509db3`, имеет успешные fast-contract checks,
  но verifier всё ещё вызывается из PR-editable workflow. Независимая защита от
  отключения verifier — отдельный [P0 в #496](https://github.com/maksimp6/Chat/issues/496#issuecomment-6106482490),
  не закрытый результат engine tests. Никакие настройки этим PR не меняются.

## Запись допуска и handoff

Каноническая задача — запрос владельца о сохранении уроков и frontend-аудите;
отдельный Issue не создаётся. Принятые критерии: документация и проектные навыки,
проверяемые источники, безопасная публикация, одна ветка и Draft PR, без merge.
Основной владелец этапа — Docs Engineer; backend — Codex с shell checkout,
Git read/push и GitHub connector для metadata/PR. Ветка:
`docs/reusable-lessons-frontend-audit`, исходный head — указанный `master`.

Этапы: source inventory → исторический синтез → локальная проверка → Draft PR.
Стоп-условия: неполученный исторический материал, секрет в diff, неясный источник,
нечистый formatter gate или отсутствие подтверждённой публикации. Исторический
материал получен до публикации. Следующий владелец — ревьюер/владелец продукта;
реализации gaps требуют отдельного решения. Точный опубликованный head и
результаты проверок фиксируются в Draft PR, чтобы не создавать самоссылочный SHA.
