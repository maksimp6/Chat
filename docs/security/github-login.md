# Shared local access

Updated 2026-10-24 at the owner's request. GitHub visitor login, short-token paths,
identity cookies and visitor bootstrap secrets have been removed. Open `/` directly.
The compatibility endpoint `POST /api/users/bootstrap` returns the installation's
shared local ID and does not issue a cookie or token.

Every reachable client can use the web, MCP and administration APIs. New records
use `LOGOS_OWNER_ID` or `default`; this setting is data/accounting metadata.
Existing conversations and runtime sessions remain accessible. Old login-related
environment variables have no effect.

Authentication required by external providers (Yandex, Claude, Cloud.ru, SSH and
other connected services) remains part of those integrations. No real credentials
belong in the repository, traces, tests or application logs.
