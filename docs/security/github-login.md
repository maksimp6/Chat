# Sign in with GitHub

Alice Pro can sign users in with a GitHub account (`identity/github_oauth.py`).
It is off until a GitHub OAuth App is configured.

## Flow

1. `GET /auth/github/login` sends the browser to GitHub with a random `state`
   kept in a short-lived HttpOnly cookie.
2. `GET /auth/github/callback` checks `state`, exchanges the code, reads the
   account id and login from `GET https://api.github.com/user` (scope
   `read:user`), and discards the GitHub access token.
3. The GitHub account is linked to an Alice user in `github_accounts`:
   a returning account signs in as the same user; a new account is linked to
   the browser's current anonymous user (its history is kept) or to a new user.
   A user already backed by GitHub never gains a second GitHub account. The
   promoted user's installation id is retired, so anonymous bootstrap can no
   longer mint tokens for it.
4. The browser gets a fresh `alice_user_token` cookie. When the short-token
   gate is on and the account id is allowlisted, it also gets the gate session, so
   GitHub sign-in replaces the secret URL.

`GET /api/auth/me` reports the signed-in GitHub login; `POST /auth/logout`
clears both cookies. With the gate on, a browser opening a gated page sees a
"Войти через GitHub" link instead of the JSON 401.

## Configuration

| Variable | Meaning |
| --- | --- |
| `ALICE_GITHUB_CLIENT_ID` | OAuth App client id |
| `ALICE_GITHUB_CLIENT_SECRET` | OAuth App client secret |
| `ALICE_GITHUB_REDIRECT_URI` | Registered callback, default `https://maxxxpavlov.ru/auth/github/callback` in production |
| `ALICE_GITHUB_ALLOWED_IDS` | Comma-separated numeric GitHub account ids allowed while the gate is on (ids, because logins can be renamed and reassigned) |

The current legacy `production-deploy.yml` path reads the client id and secret from GitHub repository/environment secrets `ALICE_GITHUB_CLIENT_ID` and `ALICE_GITHUB_CLIENT_SECRET`, and the allowlist from `ALICE_GITHUB_ALLOWED_IDS`. That workflow passes the values to `deploy/production/server.sh` over the existing SSH stdin handoff, and the application currently consumes them as runtime environment variables.

This is a **working compatibility deployment path**, not the target secret architecture. Production deployment ownership/cutover is tracked by #869, and canonical secret resolution/migration by #755. New deployment paths must not copy this mechanism merely because the legacy workflow still uses it. The GitHub OAuth consumer should move to the canonical Secret Store boundary through an explicit tested cutover; until then, removing these environment variables would break the current login implementation.

The OAuth App must use the homepage `https://maxxxpavlov.ru` and the callback
`https://maxxxpavlov.ru/auth/github/callback`. Visitors on `maxxxpavlov.online`
are sent to the `.ru` host first, because an OAuth App has one callback host.
Cookies are per host, so an anonymous history started on `.online` is not
linked; the GitHub account gets the `.ru` browser's anonymous user or a new one.

## Limits

- Signing in issues a new token for the user, which signs out that user's
  other browsers (the anonymous bootstrap behaves the same way).
- Preview deployments under `/preview/...` are not wired for GitHub sign-in.


## Migration boundary

Current code reads `ALICE_GITHUB_CLIENT_ID` and `ALICE_GITHUB_CLIENT_SECRET` from the process environment. A future #755 migration must preserve the OAuth behavior while changing only the credential-resolution boundary.

Acceptance for that cutover must prove at least: configured login, missing/revoked secret failure, callback/code exchange, allowlist behavior, no secret leakage, and rollback to a known-good secret version. A successful SSH deploy alone is not evidence that the OAuth secret consumer has migrated.
