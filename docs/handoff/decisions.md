# Решения владельца

## 2026-10-06: SSH не является каноническим production path

- SSH production deployment считается legacy и не должен становиться новым обязательным путём.
- Исходный handoff формулировал это как «только `cloudru-rdc.yml`». После появления Alice Dev это слишком узко: RDC — отдельный legacy desktop/browser сервис, а lightweight coding worker имеет собственный lifecycle.
- Для каждого сервиса каноничен его Cloud.ru workflow/config contract; новый сервис не следует называть RDC только из-за исторического происхождения.
- Legacy RDC выключается только после доказанного replacement/cutover, а не заранее.

## 2026-10-06: Alice Dev отделён от RDC

- Lightweight MCP coding/execution worker называется **Alice Dev** (`alice-dev-*`).
- Target: 0.2 vCPU / 512 MiB, min=0/max=1, scale-to-zero; Chromium отсутствует.
- #923 исправил live deploy после Cloud.ru HTTP 499.
- Run `37490250904` на master `d1c235b` успешно прошёл deploy, public health и MCP end-to-end smoke.
- Старый `rdc-22706bfa6066` пока остаётся fallback и не считается Alice Dev.

## 2026-10-06: авторизация Alice Dev

- Текущий short token — временный bootstrap-механизм, а не желаемый UX.
- Не плодить отдельные ручные service tokens для каждого worker.
- Конечная цель — OAuth, чтобы пользователь не копировал токен вручную.
- Наблюдения о других публичных MCP/Cloud.ru не меняют этот контракт сами по себе.

## 2026-10-06: Memory DB вместо SQL

- Целевое состояние — file-native `alice.memory`, без SQL как authoritative runtime store.
- #920: conversation ownership уже authoritative в FileMemoryDB после verified legacy import.
- #921: users + GitHub accounts + migration metadata уже один atomic aggregate; runtime identity после cutover SQL не читает.
- File-native consumers одного процесса используют общий writer/barrier на путь `alice.memory`.
- Миграция идёт волнами; отсутствие полного cutover не отменяет уже shipped boundaries.

## 2026-10-06: шифрование backup Memory DB (#479 → #913)

Канонический Issue: #913. На него ссылаются #479 и #776.

- Обязательное envelope-шифрование на стороне приложения до загрузки: на каждый snapshot свой DEK, DEK обёрнут KEK из KMS или secret-managed хранилища.
- В bucket нет plaintext backup и plaintext DEK. Ключей нет в backup, образе и репозитории.
- Restore проверяет целостность и расшифровку и падает fail-closed при отсутствующем или неверном ключе.
- Ротация KEK означает только перешифрование DEK.
- Обязателен тест восстановления зашифрованного snapshot.
- Provider-side encryption только второй слой и не заменяет это требование.

Состояние master `d1c235b`: durable backup/restore foundation есть, #913 остаётся открытым production hardening.
