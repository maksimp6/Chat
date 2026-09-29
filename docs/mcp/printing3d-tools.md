# 3D Printing Universal / MCP tools

3D business tools регистрируются в общей категории `3d` Tool Registry и
проходят через `UniversalToolExecutor`.

Поддерживаемые transports: `responses_api`, `local_agent`, `mcp`.

## Owner identity

Инструменты не принимают доверенный `owner_id` из model/caller arguments.

Owner определяется:

1. из `user_id` доверенного Universal Tool Call context;
2. при отсутствии такого context — через текущую backend owner identity.

Это сохраняет owner isolation независимо от transport.

## Tools

| Tool | Режим | Approval | Назначение |
|---|---|---|---|
| `printing3d.finance.assess` | read-only, low risk | нет | AI-first оценка покупки/кредита |
| `printing3d.finance.status` | read-only, low risk | нет | Сохранённый finance plan и фактическая окупаемость |
| `printing3d.finance.plan.set` | write, medium risk | да | Сохранить принятые условия финансирования |
| `printing3d.treasury.summary` | read-only, low risk | нет | Фактический P&L 3D-направления |
| `printing3d.orders.list` | read-only, low risk | нет | Owner-scoped очередь заказов |

## `printing3d.finance.assess`

Это основной AI-first инструмент для сценария «не заставлять пользователя
вручную заполнять форму».

Он принимает неполный набор условий. Неизвестные значения могут быть `null`.
Инструмент:

- объединяет переданные значения с уже сохранённым finance plan;
- использует фактическую прибыль текущего месяца как fallback, если она есть;
- использует явный/agent-provided forecast, если он передан;
- возвращает `missing_fields`, если условий недостаточно;
- не сохраняет результат;
- не выбирает банк и не принимает решение за пользователя;
- не придумывает PSK.

При достаточных данных расчёт включает cost basis, upfront cash, total repayment,
financing cost, required monthly profit, payment coverage и estimated payback.

## `printing3d.finance.plan.set`

Записывает принятый finance plan и поэтому:

- `read_only=false`;
- risk level: `medium`;
- `requires_approval=true`.

AI-first assessment и запись плана намеренно разделены: модель может исследовать
и пересчитывать варианты без write-side effects, а выбранные условия сохраняются
только через approval boundary.

## Default discovery

Если разговор/запрос не задаёт собственный `active_tool_categories`, категория
`3d` входит в стандартный набор локальных категорий.

Явная конфигурация разговора или запроса имеет приоритет. Поэтому пользователь
может сузить доступный tool set, и default не должен насильно возвращать `3d`.

## Hosted web search

Hosted Yandex `web_search` теперь opt-out:

- если `tools_config.web_search` отсутствует, search tool доступен;
- `tools_config.web_search.enabled=false` явно отключает его;
- default `search_context_size` — `medium`;
- `allowed_domains` и `blocked_domains` сохраняются;
- `tool_choice=auto` не изменён, поэтому наличие search tool не означает поиск
  на каждом turn;
- `file_search` и `code_interpreter` не становятся включёнными по умолчанию.

Это позволяет Alice сначала самой добывать публичные цены/условия, а затем
передавать найденные значения в `printing3d.finance.assess`.

## Browser approval после #562

Browser capability использует action-level approval:

- `navigate`, `inspect`, `screenshot`, `assert_state` — не требуют approval
  только из-за самого browser tool;
- `click`, `fill` — требуют approval.

Статический `requires_approval=true` у других tools продолжает действовать как
раньше.

Это правило описывает policy boundary. Оно не является доказательством того, что
конкретный cloud/local browser adapter настроен или production-ready.

Связанные документы:

- [3D Printing Business](../printing3d.md)
- [3D HTTP API](../api/printing3d.md)
