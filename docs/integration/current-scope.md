# Интеграционный реестр

**Дата среза:** 27 сентября 2026 года. **Проверенная база `master`:**
`de8f97c8342c8c073c84885bf8f301bba5692b33`.

Это датированный реестр, а не автоматически обновляемый статус GitHub.
Перед новым действием повторно проверьте refs, PR и CI. Требования ведутся в
[#343](https://github.com/maksimp6/Chat/issues/343),
[#350](https://github.com/maksimp6/Chat/issues/350) и
[#351](https://github.com/maksimp6/Chat/issues/351).
[Границы интеграции](../architecture/integration-coordination.md) не дублируют
эту таблицу.

## Что уже вошло в master

«Слито» ниже означает факт включения кода. Это не подтверждение деплоя, зелёного
CI текущего `master` или выполнения всех критериев родительской задачи.

| PR | Изменение | Статус |
| --- | --- | --- |
| [#363](https://github.com/maksimp6/Chat/pull/363) | Runtime owner policy и `RuntimeLoader` из #347/#349 | Слит; исторический merge-коммит `64611a1900891aad0f421c94e73ffc7162942fc3` |
| [#359](https://github.com/maksimp6/Chat/pull/359) | Host-managed preview workflow | Слит; включение и развёртывание проверяются отдельно |
| [#360](https://github.com/maksimp6/Chat/pull/360) | Поведение оболочки при сбое bootstrap | Слит; полная задача #227 не завершена этим PR |
| [#361](https://github.com/maksimp6/Chat/pull/361) | MCP-вызовы в dispatcher scope | Слит |
| [#366](https://github.com/maksimp6/Chat/pull/366) | Runtime-scoped storage contract и локальный адаптер | Слит; это не готовая интеграция Google Drive |
| [#369](https://github.com/maksimp6/Chat/pull/369) | Основа маршрутизации агентов диалога | Слит; это не весь пользовательский интерфейс #116 |
| [#370](https://github.com/maksimp6/Chat/pull/370) | Изоляция tool/MCP-выполнения | Слит |
| [#374](https://github.com/maksimp6/Chat/pull/374) | Проверки подписываемого Android release | Слит; рабочий выпуск APK ещё требует отдельного подтверждения |
| [#377](https://github.com/maksimp6/Chat/pull/377) | Контракт исполнения плагинов | Слит; не объявляет всю платформу #254 завершённой |
| [#378](https://github.com/maksimp6/Chat/pull/378) | Dispatcher-scoped filesystem | Слит |
| [#396](https://github.com/maksimp6/Chat/pull/396) | Наблюдаемость локального runtime в trace | Слит; вершина проверенной базы |

Старые coordination PR
[#364](https://github.com/maksimp6/Chat/pull/364) и
[#365](https://github.com/maksimp6/Chat/pull/365), а также staging/sync PR
[#355](https://github.com/maksimp6/Chat/pull/355) и
[#356](https://github.com/maksimp6/Chat/pull/356) закрыты без слияния.
Не ставьте их заново в очередь и не подменяйте статус «закрыт» статусом «слит».

## Незавершённая работа

| Задача / PR | Зафиксированное состояние | Следующая проверка |
| --- | --- | --- |
| [#398](https://github.com/maksimp6/Chat/pull/398) / #397 | Открыт; head `b3f7f30cae46ec66ed13ddcb57f940eec842bc0e`; включает привязку Claude job к `production` | Review, CI и реальная доступность OAuth в корректном окружении |
| [#399](https://github.com/maksimp6/Chat/pull/399) / #227 | Открыт; head `ad246e32e99dd90279d26272473137cb8a090a94`; исправляет ложные срабатывания валидатора | Остальные frontend-контракты и общий CI |
| [#400](https://github.com/maksimp6/Chat/pull/400) | Открыт; head `30f01211a09be327415ada94d161b0c675617476`; документация Cloud.ru skill | Проверка skill и обязательных checks; не является облачным backend |
| [#401](https://github.com/maksimp6/Chat/issues/401) | Контракт snapshot/finalize согласован; опубликованная реализация этой задачи не подтверждена | Восстановить запуск исполнителя, затем проверить код и billing invariant |

Документационная уборка не закрывает задачи #116, #195, #227, #254, #340 или #401.
Наличие foundation-кода не подменяет их полные критерии готовности.

## Зафиксированные блокеры

| Проверка | Наблюдение | Доказательство |
| --- | --- | --- |
| CI базового master | Frontend policy останавливается на 16 сообщениях о повторяющемся тексте; Android не находит `cryptography==50.0.1` для выбранной Android/Python-сборки | [CI 36322568803](https://github.com/maksimp6/Chat/actions/runs/36322568803) |
| Security базового master | Zizmor сообщает о незакреплённых Actions в `ci.yml` и сохранении checkout credentials | [Security 36322568775](https://github.com/maksimp6/Chat/actions/runs/36322568775) |
| CI #399 | Frontend policy проходит для 42 JS-файлов, но следующая проверка падает: `Dozzle declarative action missing` | [CI 36328517593](https://github.com/maksimp6/Chat/actions/runs/36328517593) |
| Исполнитель #401 | Claude Action завершился до исполнения задачи: отсутствуют доступные credentials | [Claude 36331058134](https://github.com/maksimp6/Chat/actions/runs/36331058134) |
| Preview | Workflow содержит gate `ALICE_HOST_PREVIEW_ENABLED`; пропуск проверки не доказывает рабочий host или деплой | [Workflow](../../.github/workflows/preview-deploy.yml) |

Это сведения об указанных запусках, не утверждение о неизменности внешних
сервисов. Не исправляйте Android простым понижением версии криптографии без
отдельной проверки совместимости и безопасности.

## Порядок продолжения

1. Устранить общие блокеры проверки: frontend в своей области, Android и Actions
   security в соответствующих сфокусированных изменениях. Не отключать проверки.
2. Довести #398 до принятия. После изменения workflow в `master` запуск задачи
   должен использовать новую версию workflow; старый failed run не служит
   доказательством исправления. Проверять фактические логи авторизации и работы.
3. Выполнить #401 с [контрактом trace](../architecture/execution_trace.md),
   опубликовать PR и проверить отсутствие повторных начислений.
4. Продолжать остальные подсистемы по зависимостям, не повторяя уже слитые
   foundation PR и не смешивая документацию skill с реализацией облачных сервисов.

## Обновление реестра

Для следующего среза фиксируйте дату, проверенный SHA `master`, head каждого
активного PR, результат checks и ссылку на доказательство. Отдельно указывайте
статусы кода, CI и развёртывания. Не публикуйте токены, приватные ключи или
секретные URL с credential-префиксом.

Изменения проверяются от дешёвых к дорогим. Markdown требует проверки ссылок,
команд и форматирования; runtime требует поведенческих тестов; изменение схемы
требует SQLite/PostgreSQL. Полный обязательный GitHub CI не заменяется локальным
отчётом. Слияние и деплой не разрешаются этой страницей автоматически.
