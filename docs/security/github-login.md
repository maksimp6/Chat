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
   gate is on and the login is allowlisted, it also gets the gate session, so
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
| `ALICE_GITHUB_ALLOWED_LOGINS` | Comma-separated logins allowed while the gate is on |

Production reads the client id and secret from the repository secrets
`ALICE_GITHUB_CLIENT_ID` and `ALICE_GITHUB_CLIENT_SECRET`, and the allowlist from
the repository variable `ALICE_GITHUB_ALLOWED_LOGINS` (default `maksimp6`). They
reach the server over the deploy SSH session's stdin, like `ALICE_SHORT_TOKEN`.

The OAuth App must use the homepage `https://maxxxpavlov.ru` and the callback
`https://maxxxpavlov.ru/auth/github/callback`. Visitors on `maxxxpavlov.online`
are sent to the `.ru` host first, because an OAuth App has one callback host.

## Limits

- Signing in issues a new token for the user, which signs out that user's
  other browsers (the anonymous bootstrap behaves the same way).
- Preview deployments under `/preview/...` are not wired for GitHub sign-in.
