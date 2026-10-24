# Alice Mouse — модуль управления указателем (staging)

Версия: модуль для интеграции, **не развернут в root-сервисе**. Issue #650 / PR #1082.

## Компоненты

| Компонент | Назначение |
| --- | --- |
| `alice_mouse.Command` | Строгая типизация и ограничения move/click/right/scroll/down/up |
| `MouseModule.issue()` | HMAC-SHA256 grant от доверенного контроллера, TTL 300 мс |
| `MouseModule.dispatch()` | Проверка HMAC, epoch/session, последовательности и возраста команды |
| `Cursor` | Ограничение координат, смена ориентации/разрешения |
| `RecordingBackend` | Безопасный тестовый backend без реального ввода |
| `RootSocketBackend` | Опциональный Unix transport; **не разрешён к включению в production** |
| `tests/alice_mouse_stage/` | C v10, fault injection, HTTP/C verifier и визуальный Alice Pet |

## Пример безопасного использования

```python
import secrets
from alice_mouse import Command, MouseModule, RecordingBackend

backend = RecordingBackend()
mouse = MouseModule(backend, focus_ok=lambda: True)
session = mouse.start(authorized=True, key=secrets.token_bytes(32))
packet = mouse.issue(Command("move", 8, -2), authorized=True)
point = mouse.dispatch(packet)
mouse.close()
assert point == (1178, 538)
```

Значение `authorized=True` допустимо **только внутри уже проверенного серверного контекста**, а не как поле внешнего HTTP JSON. Сервер должен отдельно проверить владельца устройства, роль controller и разрешённое foreground-приложение. Код в этом PR не выставляет HTTP endpoint.

## Архитектурная граница и ограничения

```text
Authenticated Live Server
    -> trusted signer / session lease
    -> root-owned independent verifier [НЕ ПОДКЛЮЧЁН]
    -> root broker and C daemon v10 [STAGING]
    -> /dev/uinput [PRODUCTION v1 НЕ МЕНЯТЬ]
    -> Android InputReader -> pointer / Overlay
```

Сейчас `MouseModule` выполняет подписание и проверку в одном процессе для изолированных тестов. Это **не доказывает независимую root-авторизацию** и не мешает другому коду, имеющему доступ к старому root socket, обходить гранты. RootSocketBackend оставлен неактивным по умолчанию: `enabled=True` — только для контролируемых испытаний после независимой проверки политик. Не переносить ключ, живую БД, токены, настоящие screenshot/frame или /data/adb в GitHub.

Критерии cutover:
1. Независимая проверка session issuance и root-owned signer/verifier, защита от обхода через существующий plaintext broker.
2. Защита root-бинарников от редактирования из Termux, SELinux в Enforcing.
3. Физический E2E BTN_UP, UI_DEV_DESTROY, rotation, screen freshness, timeout, restart и rollback.
4. Установка Overlay только после подписанного артефакта, проверок и ручной приёмки.
5. Единое владение задачей #650, CI и review, **без merge сейчас**.

## Локальные проверки на Redmi 9

```sh
python -m pytest -q tests/test_alice_mouse_module.py \
  tests/alice_mouse_stage/test_alice_mouse_signed_broker_v4_candidate.py \
  tests/alice_mouse_stage/test_cursor_stage.py
```

C fault harness, BTN/SYN tests и teardown — отдельно в `tests/alice_mouse_stage`.
