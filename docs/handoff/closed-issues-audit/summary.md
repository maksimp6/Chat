# Аудит CLOSED Issues maksimp6/Chat: итог

Проверены все 268 CLOSED Issues (на 2026-10-06, master `dfda42c`), в 7 партиях: [batch-1](batch-1.md) … [batch-7](batch-7.md). Кодовых PR и изменений в docs не делал.

## Переоткрыты (6)

| Issue | что потеряно | как закрылся |
|---|---|---|
| #538 | Repository Intelligence: из обещанного есть только `query_affected()` | «Closes #538» в squash-коммите среза |
| #869 | SSH → RDC: docs и `production-deploy.yml` не изменены | вручную, PR #871 в draft |
| #545 | Ошибка разбора SQLite timestamp (`db.py:101,113`), воспроизводится | not planned без пояснения |
| #226 | Нет WSGI-сервера и сжатия (`Dockerfile:29` → `app.run`) | PR-срез с «Closes #226» |
| #470 | Docs Work Map / trips.db | перенесён в #551, который закрыли узким PR #833 |
| #569 | Навигация docs по role-based agent office | тот же путь через #551 |

## Остаток дописан в открытые канонические Issues

#496 (статус Format из #822), #695 (конкретные дефекты из #692–#694), #776, #783, #592, #604, #551, #576, #761, #326, #538. Ссылки на владельца также оставлены на закрытых #652, #769, #442, #447, #427, #423, #757, #703, #229, #549, #600, #595, #607, #568 и #5.

## Решение владельца

- #479 → новый канонический #913: обязательное application-side envelope encryption backup Memory DB, KEK в KMS или secret-managed хранилище, тест восстановления зашифрованного snapshot (решение 2026-10-06). Ссылки оставлены на #479 и #776.

## Как Issues закрываются по ошибке

1. `Closes #N` в коммитах из `alice.yml` и бот-веток `claude/issue-*`: squash-merge одного среза закрывает весь родительский Issue (#538).
2. Фраза «Do not close #N» в теле PR сама закрывает Issue (#703, #757).
3. Issue, привязанный к PR через Development, закрывается при merge этого PR (#757).
4. Перенос («absorb») в Issue, который потом закрыли, теряет перенесённый scope (#470, #569).

## Расхождения в документации (для потока docs)

- `docs/production-deployment.md` описывает только SSH (#869).
- `docs/integrations/cloudru-docs-mirror.md`: «Статус: planned», хотя #428 закрыт как not planned и crawler-а нет.
- Нет страницы Work Map (#470) и навигации по role-based office (#569).
- В теле #409 устарели описания #791 и #843.
