# Внешний PostgreSQL для Алисы

Цель — PostgreSQL 17 на уже выбранном и разрешённом сервере/сервисе с публичным
endpoint. Этот комплект не создаёт VM. Для другого PostgreSQL провайдера его
managed-настройки должны обеспечивать те же свойства; системные файлы ниже
применимы только к самостоятельно администрируемому серверу.

## Сеть: сначала подтвердить маршрут

Cloud.ru прямо указывает, что Container Apps не подключается к Evolution
Managed PostgreSQL; внешний PostgreSQL доступен через интернет. Сам Managed
PostgreSQL использует внутренние адреса. Нельзя обещать ему «включить public IP».

Для текущего профиля нужны **гарантированные** исходящие адреса приложения
(и backup runner), записанные в `allowed_client_cidrs` и обеих firewall-политиках.
Один результат проверки IP из контейнера не доказывает стабильность egress при
cold start, смене ревизии/зоны или масштабировании. `egress_evidence` хранит
ссылку на подтверждение провайдера/сетевое решение, а не секрет.

Если Container Apps не даёт подтверждённый постоянный egress, этот профиль
пока не готов к запуску. Нужно отдельно выбрать поддерживаемое сетевое решение
либо другой согласованный профиль доступа (например, провайдерский endpoint
с обязательным mTLS и собственной защитой). Не заменять пустой allowlist на
`0.0.0.0/0`/`::/0`, не обещать несуществующий NAT/VPC connector и не считать
обычный HTTPS API Gateway TCP-прокси для PostgreSQL. mTLS здесь не реализован.

## Сервер

1. Выделить базу и диск; обеспечить обновления PostgreSQL/ОС, синхронизацию
   времени, мониторинг места, backup и ограниченный административный доступ.
   Начальный профиль рассчитан на >=2 GiB RAM, не является sizing-гарантией.
2. DNS A/AAAA направить на действительный endpoint. Сертификат должен содержать
   этот DNS SAN; при подключении по IP нужен именно IP SAN. Установить full chain,
   держать закрытый ключ только на сервере с доступом PostgreSQL (обычно 0600
   и владелец postgres). Настроить продление и проверку срока действия.
3. Внести `postgresql.conf.example`, сохранив distribution-specific paths.
   `listen_addresses` — адрес реального интерфейса за NAT. Не класть PGDATA,
   WAL или live SQLite в S3 mount. Не отключать fsync ради скорости.
4. Применить cloud firewall: inbound TCP/5432 только application/backup CIDRs;
   SSH/admin — отдельно через разрешённый канал. `firewall.nft.example` —
   дополнительный guard для host-network PostgreSQL, с пустыми allowlists
   безопасно закрыт. Проверить `nft -c -f`, не делать `flush ruleset`.
   Он не заменяет firewall провайдера и **не защищает Docker port publishing**
   через FORWARD/NAT; для контейнерной БД нужна отдельная проверенная политика.
5. Подготовить HBA из `pg_hba.conf.example`, заменить CIDR-заглушки. IPv4 и IPv6
   независимы. Локальный администратор использует peer, внешний superuser login
   закрыт. SCRAM + hostssl обязательны. Проверить `pg_hba_file_rules.error`,
   текущие config paths и порядок правил до открытия трафика. Изменения listen/
   memory требуют restart, HBA/cert обычно reload; проверить фактический результат.
6. Для новой базы выполнить `bootstrap.sql` от локального postgres через
   `psql -X -v ON_ERROR_STOP=1 -f ...`. Задать пароль через интерактивную
   `\password alice_app` или существующий защищённый provisioner. Не передавать
   пароль в аргументах/SQL issue. Затем применить `backup-role.sql` и отдельно
   задать пароль `alice_backup`. Хранить их в Secret Management.

Роль `alice_app` имеет владение **своей** БД/схемой, но не superuser/CREATEDB/
CREATEROLE/REPLICATION/BYPASSRLS. Это осознанная совместимость с `db.py`, который
создаёт и изменяет таблицы при старте. Только SELECT/INSERT/UPDATE/DELETE сейчас
недостаточно. Выделение отдельного migrator — будущая доработка с отдельным
тестом. Не выдавать `alice_app` членство в других grantable PostgreSQL-ролях:
preflight отклоняет любые прямо или транзитивно выданные membership, чтобы
исключить дополнительные права custom и predefined roles. Единственное
исключение — невыдаваемое implicit membership владельца текущей базы в
`pg_database_owner`. Миграции запускать одним процессом;
`max_instances=1` само по себе не
предотвращает overlap ревизий. Runtime limit 20 соединений и server limit 60 —
стартовый бюджет, включая две ревизии, backup и администрирование. Замерить
фактическое число соединений под нагрузкой; env для вымышленного pool не вводить.

## Runtime DSN и CA

`db_backend.py` передаёт `ALICE_DATABASE_URL` в psycopg/libpq. Форма значения
ниже только иллюстрация; все зарезервированные символы логина/пароля нужно
percent-encode, а секрет целиком внедрить через защищённое окружение:

```text
postgresql://alice_app:REPLACE_URLENCODED_PASSWORD@REPLACE_POSTGRES_FQDN:5432/alice?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt&ssl_min_protocol_version=TLSv1.2&gssencmode=disable&channel_binding=require&connect_timeout=5&application_name=alice-pro
```

`verify-full` проверяет доверенную цепочку и имя сервера. Не подменять его на
`require` или `prefer`. Для публично доверенного сертификата проверить наличие
нужного root CA в системном bundle **в финальном образе**. Для своей CA положить
только публичный root/intermediate сертификат в tracked
`deploy/cloudru/trust/db-root-ca.crt`, проверить fingerprint отдельным доверенным
каналом и указать `/app/deploy/cloudru/trust/db-root-ca.crt` в DSN. Dockerfile
копирует tracked tree в `/app`; публичный файл должен читаться UID 10001.
Никогда не коммитить private key. Смена CA требует overlap trust и проверки
нового образа до удаления старого root. DSN-пути относятся к контейнеру, а не CI.

## Проверка перед переключением

- Из **целевого runtime** выполнить `cloudru_deploy_preflight.py --check-db`:
  реальное TLS-соединение, правильная БД/роль, отсутствие elevated privileges.
  Probe делает только SELECT с read-only transaction и ограниченными timeout.
- Проверить отрицательные сценарии: посторонний IP блокируется, неверная CA,
  hostname или пароль не проходит; SSL-disabled соединение отклоняется.
  Использовать защищённый тестовый клиент, не печатать libpq exceptions с DSN.
- В staging проверить создание схемы, CRUD, restart/cold start, read-after-redeploy,
  владельческий login и сохранность зашифрованных provider credentials.
- Публичный /healthz сам по себе не доказывает доступ к БД. Прежде чем направлять
  пользователей, нужен backup/restore test и запись source SHA/image digest.

## Backup и восстановление

Стартовая целевая политика: логический backup ежедневно, хранение 14 суток,
пробное восстановление еженедельно; предлагаемые RPO 24h/RTO 2h подтвердить
измерением. Для меньшего RPO нужен отдельный работающий WAL/PITR pipeline.

Backup runner должен быть постоянно доступным scheduler/Container Job, а не
таймером внутри scale-to-zero приложения. Использовать PostgreSQL client 17
для сервера 17; major client не ниже major сервера. Настроить libpq service из
`pg_service.conf.example`, `PGSERVICEFILE` и `PGPASSFILE` с правами 0600; в
PGPASSFILE специальные `:` и `\` экранировать. Секретов в argv нет:

```bash
# Только в подготовленном защищённом runner с уже установленными переменными.
# BACKUP_PRIVATE_DIR — отдельный каталог 0700 на зашифрованном временном диске.
umask 077
PGSERVICE=alice-backup pg_dump --format=custom --no-owner --no-acl --file="$BACKUP_PRIVATE_DIR/alice.dump"
pg_restore --list "$BACKUP_PRIVATE_DIR/alice.dump" > /dev/null
```

Далее сохранить dump в **закрытый** Object Storage bucket с versioning/retention
и SSE-KMS, если такой режим реально настроен и проверен. У runtime и backup
разные S3-права; backup writer не должен удалять все версии. Хранить checksum,
время, schema/source version и key-version ID отдельно от секретов. Проверить
checksum после загрузки. Не считать незавершённый KMS adapter готовым шифрованием.

Восстанавливать в новую изолированную БД: подготовить роли/ownership, выполнить
`pg_restore --exit-on-error --no-owner --no-acl` от роли-владельца целевой БД,
проверить таблицы, CRUD и расшифровку provider credentials тем же
`ALICE_PROVIDER_CREDENTIAL_KEY`. Никогда не применять `--clean` к рабочей БД.
Зафиксировать реальный RTO; только после успешного restore удалять старые
backup по retention. Ошибки dump/upload/restore, возраст последнего успешного
backup, место на диске, connections, TLS expiry должны давать alert.

PostgreSQL JSON logs тоже могут содержать данные в сообщениях ошибок. Ограничить
доступ/retention и редактировать перед отправкой в общие логи; отсутствие SQL
statement logging не является полной очисткой PII.

## Первичные источники

- [Container Apps: подключение БД](https://cloud.ru/docs/container-apps-evolution/ug/topics/faq__database-connection)
- [Managed PostgreSQL: сеть](https://cloud.ru/docs/paas-postgresql/ug/topics/faq__connecting-from-local-machine)
- [PostgreSQL 17: HBA](https://www.postgresql.org/docs/17/auth-pg-hba-conf.html)
- [PostgreSQL 17: TLS сервера](https://www.postgresql.org/docs/17/ssl-tcp.html)
- [PostgreSQL 17: проверка сертификата клиентом](https://www.postgresql.org/docs/17/libpq-ssl.html)
- [pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html)
- [pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html)
