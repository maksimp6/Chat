# Anonymous user bootstrap

The Android first-run flow generates an installation identifier and registers it through POST /api/users/bootstrap. Registration is idempotent, so restarting the application reuses the same anonymous user ID.

The anonymous user ID is not an authentication credential and is never accepted as the trusted Treasury owner by itself. Bootstrap also issues a random owner authentication token. The server stores only its SHA-256 hash and returns the raw token to the client once per bootstrap response.

The Android client stores the token in private preferences and installs it as the `alice_user_token` HttpOnly cookie in the local WebView. Sensitive operations resolve the owner from that verified token or another trusted server-side authentication context. Client-controlled `owner_id` values and the non-sensitive `X-Alice-User-ID` header are not used for Treasury authorization.

When bootstrap is unavailable, the client may continue using a previously stored token. A fixed `ALICE_OWNER_ID` remains available only as an explicitly configured single-user deployment fallback.
