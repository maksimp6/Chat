# Alice Pro — полный конвейер Bambu Lab A1 mini

Ответственный Issue: #1096. Каталог моделей #1112 / PR #1114. Слайсер #1101. Preflight #1109.
Этот Draft-срез реализует контракты, но ещё не развёрнут в Alice production.

## Единый сценарий

1. **Выбор модели:** каталог #1114 с картинками реальной геометрии. Исходник/версия/материал/пластина, обязательные измерения. Неизвестные посадки и прочность: BLOCKED.
2. **CAD / QA:** генерация STL/3MF и отчёт геометрии с SHA-256, допустимый габарит 180 мм.
3. **Нарезка:** pinned Bambu Studio CLI из #1101 на GitHub runner с памятью, а не 512 MiB cloud RDC. PLA/PETG, A1 mini 0.4 мм, фактический материал и стол. Требуется ровно один готовый .gcode.3mf с Metadata/plate_1.gcode; не ZIP с PNG. Нынешний #1101 режет куб — расширение для произвольного входа ещё BLOCKED.
4. **Preflight:** код #1109 должен отвергать порчу ZIP, отсутствие G-code, неправильный материал, сопло, количество пластин, температуры, опасные команды. Fail closed при скрытом fallback PETG→PLA.
5. **STAGE:** локальный Redmi 9 RDC, FTPS 990, предварительно одобренный TLS-pin до ввода пароля. На SD — каталог /model, один безопасный файл. Удалённый readback сверяет SHA-256+длину, без повторного STOR после неоднозначного таймаута.
6. **Отдельный физический START:** доверенный UI показывает файл, материал, сопло, температуру и реальный статус. Оператор явно утверждает именно этот job; отдельный HMAC grant живёт максимум 5 минут и привязан к job+SHA. До исполнения записать START_PENDING в file-native MemoryDB; повтор при неизвестном результате не выполнять. Возможен старт вручную с экрана принтера.
7. **Наблюдение:** реальная MQTT-телеметрия и камера по запросу; никаких воображаемых процентов. После FINISH → COMPLETE. При дефектах сохранять фото/наблюдения и исправлять новую ревизию CAD.
8. **Видео:** читать готовый .mp4/.avi из /timelapse или /recorder принтера без удаления SD, через TLS-pinned FTPS. Спул в owner-hash/job-id с SHA-256, MD5 и защитой от path traversal.
9. **Архив:** resumable Google Drive upload через server-side OAuth (не ChatGPT connector session), приватная папка, проверка MD5/size и file-id, затем receipt в job. При quotaExceeded или обрыве оставить видео локально и статус VIDEO_PENDING. Без автоматического удаления.

## Видеоархив и расширение

[Главная папка](https://drive.google.com/drive/folders/1lDsDj1QhOD4R2YRcfCc5qmxwlqS5AnVE)
[Октябрь 2026](https://drive.google.com/drive/folders/1rDR2xko-rIt0BZm4d1Z7OygDY6f_Zhaq)

В папке уже имеется A1_mini_timelapse_2026-10-10.mp4 (183003 байт).
Drive не обещает безлимитного бесплатного хранилища: API storageQuota.limit/usage проверяется до загрузки, резерв >=512 MiB. Число видео не ограничиваем программно, автоочистки нет; при нехватке места — явное предупреждение и перенос в отдельный storage tier Cloud.ru S3 только с бюджетным одобрением.

GitHub: код, модель, рендеры, тесты, обезличенные контрольные суммы.
Google Drive: сырые видео и фото с приватным доступом, без публичного anyone-права.
Memory DB: журнал статусов, связь job ↔ видео/Drive ID, error/retry без SQL.
SecretProvider: сертификат/доверенный fingerprint, пароль принтера, Drive OAuth refresh token, HMAC ключ.

## Статусы

REQUESTED → CAD_READY → GEOMETRY_VERIFIED → SLICED → AUDITED → STAGED → READY_FOR_USER → START_PENDING → PRINTING → COMPLETE.
Исключения: BLOCKED, FAILED, START_UNKNOWN. Видео-архив — отдельный receipt, не признак успеха физической печати.

## Состав этого PR

- printing3d/print_workflow.py: атомарный file-native lifecycle/fsync, replay-fence и approval.
- printing3d/ftps_storage.py: pinned TLS FTPS staging/readback/video-copy. **Не содержит start_print или delete.**
- printing3d/video_archive.py: Drive quota, bounded spool, resumable chunks, MD5+size+SHA proof, локальная копия не удаляется.
- tests/test_printing3d_workflow_contract.py: быстрые offline negative tests, не физический принтер.

## Точные блокеры live

- #1101 пока не принимает произвольные модели — нужен реальный slicer E2E.
- #1109 остаётся отдельным неподключённым preflight, без merge.
- У Alice пока нет собственного разрешённого OAuth Drive и secret bridge; ChatGPT Google Drive plugin не превращается автоматически в токен Alice.
- FTPS fingerprint должен быть сверён и утверждён оператором; legacy RDC scripts с unverified TLS НЕ переносить в production.
- Реальные MQTT, материал, состояние стола, разрешение START и запись видео требуют отдельного authorised device E2E.
- Текущие проверки: 19 unit contracts за 0.203 с; production и физическая печать НЕ ВЕРИФИЦИРОВАНЫ.

PR feature → develop; затем защищённый develop → master. Без автоматического merge/нагрева/печати.
