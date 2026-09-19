# Anonymous user bootstrap

The Android first-run flow generates an installation identifier and registers it through POST /api/users/bootstrap. Registration is idempotent, so restarting the application reuses the same anonymous user ID.

The anonymous user ID is not an authentication credential and must not be used as the trusted owner for Treasury or other sensitive operations. Sensitive authorization continues through the server-side identity boundary.

Registration metadata is sanitized before storage. The Android client stores the installation and user IDs in private preferences and exposes the anonymous user ID to frontend API requests for non-sensitive personalization.
