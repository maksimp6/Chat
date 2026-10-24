# #1413 — рассказ Логоса через Yandex TTS

- Канонический запрос: комментарий владельца в текущем чате 24.10.2026;
  контракт и допуск записаны в #1413, comment 6382563805.
- Принятый объём: рассказ на русском из фактического result.json, отдельная
  цитата модели, текст/аудиочасти/квитанция, bounded TTS через общий контракт,
  workflow artifact. Потоковое воспроизведение не входит в этот первый срез.
- Один исполнитель: ChatGPT, backend/infra; backend этой сессии, shell и
  GitHub connector фактически доступны. Другие агенты не запускались.
- Стадия: implementation → deterministic tests → self-review → draft PR.
- Ветка: feat/logos-runner-narration, base develop
  `8d0fb443f881cceb31d8703aa5d72ad0ffc68705`.
- Зависимость: #1569, проверенный integration snapshot
  `6a0a24764812d450e2b34a57b68392c7fb1bbc3a`; нет копирования его кода в этот PR.
- Платные model/TTS calls: NOT_RUN; новые секреты/доступы, D0, merge/deploy
  не разрешены данным этапом. Два TTS settings не включались.
- Стоп: нет contract transport, credentials или допустимого лимита; невалидный
  источник, HTTP/неаудио ответ. Нет повторов; частичный результат явно failed.
- Передача: владелец принимает текст/аудио отдельно; живой TTS требует
  согласованного расхода. Требуемые CI/review и актуальность develop проверяются
  при публикации. Локальные тесты и итоговые SHA фиксируются в PR.
- Ограничения: рассказ итоговый; аудиочасти раздельные; header check не является
  декодированием/прослушиванием; стоимость неизвестна; Android delivery NOT_RUN.

## Проверки и self-review

- 141 focused tests: narration/workflow/issue runner/generation gate — PASS.
- Расширенная выборка с launch-hold: 159 passed, 5 failed. Все пять ошибок
  tests/test_logos_launch_hold.py воспроизведены на отдельном чистом checkout
  develop `8d0fb443f881cceb31d8703aa5d72ad0ffc68705`: 18 passed, 5 failed.
  Существующие тесты ожидают прежние if/порядок шагов workflow. Они не менялись.
- Ruff новых Python-файлов, compileall (одна существующая SyntaxWarning),
  runtime validator и 20 agent skills — PASS. Canonical pre-push gate прошёл
  с чистым checkout; форматирование выполнялось только через scripts/format.sh.
- В отдельном integration checkout #1569 реальный pinned TTS contract
  пропускает один валидный вызов; неизвестный адрес блокируется с zero send.
  Подменён только requests.post; платных запросов нет.
- Self-review: перенесена озвучка после основной публикации, чтобы её отказ
  не закрывал diff/check/PR steps; исправлены lint/import issues. Проверены
  missing transport, секреты, неверный HTTP 200, усечение, лимиты и отсутствие
  повторов. Дополнительные модельные reviewers не запускались.
- Shell push недоступен: `fatal: could not read Username for 'https://github.com':
  No such device or address`. Публикация через имеющийся GitHub connector,
  без создания credentials; дерево сверяется с локально проверенным.
