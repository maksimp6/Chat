-- Только новая БД в подготовленном PostgreSQL, запуск локально как postgres.
-- Не миграция существующей схемы. Повторный запуск намеренно завершается ошибкой.
\set ON_ERROR_STOP on
SET password_encryption = 'scram-sha-256';
CREATE ROLE alice_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 20;
CREATE DATABASE alice OWNER alice_app ENCODING 'UTF8' TEMPLATE template0;
REVOKE ALL ON DATABASE alice FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE alice TO alice_app;
ALTER ROLE alice_app IN DATABASE alice SET statement_timeout = '30s';
ALTER ROLE alice_app IN DATABASE alice SET lock_timeout = '5s';
ALTER ROLE alice_app IN DATABASE alice SET idle_in_transaction_session_timeout = '30s';
\connect alice
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO alice_app;
ALTER ROLE alice_app IN DATABASE alice SET search_path = public;
-- Затем в защищённом интерактивном psql: \password alice_app
-- До установки пароля удалённый SCRAM login не работает.
