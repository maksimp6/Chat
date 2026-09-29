# 3D Printing HTTP API

HTTP endpoints модуля находятся под `/api/3d`.

Кроме `POST /api/3d/quote`, endpoints ниже owner-scoped. При отсутствии
доверенной owner identity они возвращают:

```json
{"error":"authenticated owner identity is required"}
```

со статусом HTTP 401.

## Endpoints

| Method | Path | Назначение |
|---|---|---|
| POST | `/api/3d/quote` | Рассчитать котировку без сохранения заказа |
| GET | `/api/3d/orders` | Список заказов текущего owner |
| POST | `/api/3d/orders` | Создать заказ |
| GET | `/api/3d/orders/<id>` | Получить один owner-scoped заказ |
| PATCH | `/api/3d/orders/<id>/status` | Изменить статус заказа, кроме прямого перехода в paid |
| POST | `/api/3d/orders/<id>/settle` | Зафиксировать фактическую оплату и себестоимость |
| GET | `/api/3d/treasury/summary` | Получить фактический P&L 3D-направления |
| PUT | `/api/3d/finance/plan` | Сохранить owner-scoped finance plan |
| GET | `/api/3d/finance/status?month=YYYY-MM` | Получить окупаемость и покрытие платежа |

## Quote

`POST /api/3d/quote` не требует owner identity и ничего не сохраняет.

Расчёт учитывает:

- массу материала и стоимость материала за кг;
- длительность печати и потребление принтера;
- стоимость электроэнергии;
- амортизацию за час;
- упаковку;
- резерв брака;
- комиссию площадки;
- целевую маржу.

Валюта по умолчанию: `RUB`.

Невалидные параметры дают HTTP 400 с
`{"error":"invalid quote parameters"}`.

## Заказы и статусы

Поддерживаемые статусы:

`lead`, `quote`, `accepted`, `printing`, `ready`, `delivered`,
`paid`, `cancelled`.

Обычный status endpoint не разрешает прямой переход в `paid`. Для paid нужен
settlement, чтобы фактические revenue/cost не расходились со статусом заказа.

Заказ другого owner возвращается как `order not found`; caller-supplied
`owner_id` не используется как источник доверия.

## Settlement

`POST /api/3d/orders/<id>/settle` принимает:

- `actual_revenue`;
- `actual_cost`.

Если quote уже содержит оценённую полную себестоимость, она может использоваться
как cost fallback. Если фактическая выручка или cost не могут быть определены,
settlement отклоняется.

Повторный settlement:

- без новых значений возвращает уже settled заказ;
- с теми же фактическими значениями также допустим;
- с изменёнными значениями отклоняется;
- для `cancelled` заказа отклоняется.

## Treasury summary

`GET /api/3d/treasury/summary` агрегирует заказы текущего owner по валютам и
показывает, в частности:

- количество заказов;
- open/paid orders;
- quoted revenue незакрытых/неотменённых заказов;
- settled revenue;
- settled cost;
- settled profit.

Фактические totals строятся только по settled paid orders.

Этот endpoint не изменяет существующий Treasury paper balance. 3D business P&L
и внутренний баланс AI/billing остаются разными учётными контурами.

## Finance plan

`PUT /api/3d/finance/plan` сохраняет один owner-scoped plan с полями:

- `printer_model`;
- `equipment_price`;
- `setup_cost`;
- `down_payment`;
- `credit_principal`;
- `credit_total_repayment` (может быть неизвестен);
- `monthly_payment` и `term_months`;
- `psk_percent` (может быть неизвестен);
- `currency`;
- `started_at`.

Если payment задан, term обязателен, и наоборот. Известная
`credit_total_repayment` не может быть меньше `credit_principal`.

HTTP endpoint возвращает 400 `invalid finance plan` при нарушении контракта.

## Finance status

`GET /api/3d/finance/status` читает сохранённый plan и фактическую прибыль
owner. Параметр `month` имеет формат `YYYY-MM`; без него используется текущий
UTC-месяц.

Ответ различает полный и частичный cost basis. Если итоговая сумма возврата по
кредиту ещё неизвестна, кредитная переплата также остаётся неизвестной, а не
угадывается.

Статус показывает:

- known cost basis;
- кредитную переплату, если известна;
- cumulative realized profit;
- realized profit выбранного месяца;
- остаток до известной cost basis;
- процент окупаемости;
- покрытие monthly payment фактической прибылью;
- break-even, когда его можно определить.

## Ошибки

API намеренно возвращает стабильные внешние сообщения вместо сырых exception
strings. Это предотвращает утечку внутренних stack/error details.

Связанные документы:

- [3D Printing Business](../printing3d.md)
- [3D Universal/MCP tools](../mcp/printing3d-tools.md)
