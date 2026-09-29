-- После bootstrap.sql; отдельная роль чтения для логического backup.
\set ON_ERROR_STOP on
CREATE ROLE alice_backup LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
GRANT CONNECT ON DATABASE alice TO alice_backup;
\connect alice
GRANT USAGE ON SCHEMA public TO alice_backup;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO alice_backup;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO alice_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE alice_app IN SCHEMA public
  GRANT SELECT ON TABLES TO alice_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE alice_app IN SCHEMA public
  GRANT SELECT ON SEQUENCES TO alice_backup;
-- Повторить grants для других creator roles/schemas, если они появятся.
-- При RLS понадобится отдельная проверенная стратегия полного backup.
-- Затем в защищённом интерактивном psql: \password alice_backup
