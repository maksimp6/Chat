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

## Обновление после изменения develop

Ранее указанный base/результат Gradle устарел: во время публикации в develop
попал #1577. Актуальный проверенный base:
`b9963beb65663ca90f38fdc42248b14279b6f438`. Его изменения и обновлённый AGENTS.md
прочитаны; base интегрирован в ту же рабочую ветку PR #1588.

В новом scripts/logos_once.py подключён тот же narrator с сохранением
единственного видимого шага logos и исходного exit code. Текст идёт в summary,
а упорядоченные аудиобайты/квитанция — в существующие reconstructable JSON log
chunks. Это ограниченная доставка через логи, без аудиоплеера/приёмки Android.
SpeechKit выполняется в существующем venv, без новых установок/шагов. Секреты
удаляются из цитаты до формирования отдельного narration-source; сохранён
также хеш исходной квитанции до присоединения narration.

После синхронизации проверяется расширенная выборка narration/once/workflow/
issue runner/generation gate/Gradle; прежние цифры выше относятся к старому
срезу. Пять launch-hold failures не исправляются этим PR. Итоговые SHA и
результаты обновлённых проверок фиксируются в PR после публикации.

Итоговая синхронизация: develop
`33e1bd5a7526bbeb0a236b7789a8fa868f741523` (включая Ultra и LFS runtime changes).
На этом checkout **170 focused tests passed**; в том числе два Gradle-контроля
теперь проходят. Прежний Gradle-блокер относится к старому base и не переносится
на этот срез. Новая доставка тестовых аудиобайтов проверена через реальные
emit_result/log chunks с восстановлением base64; TTS в контроле подменён.
Header/байты тестового контейнера не доказывают живое воспроизведение.

## Продолжение по команде владельца «▶ Дальше»

Допуск возобновлён: та же задача/исполнитель/PR #1588, новый проверенный develop
`5cc930233a2889710fcc6df9f46288d2107c04e1`, локальная ветка
feat/logos-narration-continue, publication branch feat/logos-runner-narration.
Инструкции AGENTS.md и scoped admission перечитаны. Merge и платные вызовы
командой продолжения не разрешены; #1569 всё ещё открыт.

Причина отсутствия backend CI доказана публичной страницей run 40083161591:
`(Line: 23, Col: 8): Unrecognized named-value: 'runner'` в root env OUT.
Такой же отказ root env EVIDENCE_DIR подтверждён в launch-smoke 40083162814.
Переменные перенесены в job env без смены путей, D0 и owner job-result policy.
Добавлен ранний offline narration/delivery pytest step в существующий backend
job; один видимый шаг ручного logos не меняется. Остальные прежние invalid
workflow (pr-court/observer) не переписываются в рамках этой задачи.

Добавлен logos_narration_receive: извлечение готовых story/receipt/ogg из
existing single-step log chunks с проверкой полноты/порядка, общего SHA,
SHA текста/аудио и безопасных имён до записи. Повторное использование каталога
запрещено. Recovery не считается декодированием или воспроизведением речи.
Регрессии включают порчу, traversal, потерю последнего frame и duplicate YAML
env keys. Числа итоговых проверок/точный HEAD публикуются в PR.

Итоговый повторный base: `7cfefcc5d54c78b8d0c746ff1692786aaff387e7`.
Обновлённый AGENTS.md («Законы Логоса») прочитан; 260 focused tests passed на
синхронизированном checkout, Ruff изменённых Python-файлов и canonical
pre-push gate PASS. Секреты/расходы/D0/merge не менялись. Новые CI-результаты
требуют проверки после публикации; прежние не переиспользуются.

GitHub отклонил runner.temp и на уровне job env (run 40083883254, line 242;
launch-smoke 40083883861, lines 26/140). Первый перенос не является исправленным
CI. Метод изменён: существующий первый clock step экспортирует OUT/EVIDENCE_DIR
через $RUNNER_TEMP и $GITHUB_ENV; GitHub expression runner.temp удалён из обоих
workflow. Реальное исполнение clock shell проверяет записанное окружение.
Duration-reporting Python, граница 30s и policy не менялись.

Дополнительно локально проверен настоящий корректный OggOpus (синтетический
тон FFmpeg, не SpeechKit): emit_result → log recovery → byte identity →
ffprobe codec=opus → полное FFmpeg decode. Живой TTS и прослушивание NOT_RUN.
