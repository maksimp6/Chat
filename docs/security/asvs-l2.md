# OWASP ASVS level 2

Alice Pro tracks the [OWASP ASVS 4.0.3](https://owasp.org/www-project-application-security-verification-standard/)
level 2 requirements that apply to it in `asvs-l2.yaml`. Each entry has a
status and either evidence (a test that proves it) or a note explaining what
is missing.

Current state: 27 requirements; 12 met, 11 partial, 2 gaps, 2 not applicable.

## Open gaps

| Requirement | Gap |
|---|---|
| V2.2.1 Anti-automation | No rate limit on the short-token and OAuth callback endpoints. |
| V14.5.3 CORS | `mcp_server/transport.py` answers `Access-Control-Allow-Origin: *`. |

## Partial, next in line

- **V14.4.3 CSP.** The policy is sent as `Content-Security-Policy-Report-Only`.
  Enforce it after the inline `style` attribute in `templates/index.html` moves
  to `static/style.css`.
- **V3.4.1 Secure cookies.** `alice_user_token` and the short-token refresh set
  `Secure` only on HTTPS requests; set it unconditionally outside local dev.
- **V7.4.1 Generic errors.** `tests/test_api_problem_details.py` tracks the
  responses that still echo exceptions; convert them to `problem()`.

## Rules

- `tests/test_asvs_checklist.py` fails if a `met` entry lacks existing evidence,
  if a non-met entry has no note, or if the number of gaps grows.
- Closing a gap: fix it, add the evidence test, set `met`, and lower
  `GAP_BASELINE` in the same pull request.
