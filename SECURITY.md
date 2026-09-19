# Security Policy

## Supported releases

Security fixes are developed against the current default branch unless a specific release is listed in the repository releases.

## Reporting a vulnerability

Do **not** open a public issue for a suspected vulnerability, leaked credential, authentication bypass, or other security-sensitive problem.

Use the repository's GitHub **Security** tab and private vulnerability reporting when it is enabled. If private reporting is not available, contact the maintainer privately through GitHub and include enough information to reproduce the issue safely.

Please include:

- affected component and version/commit;
- impact and attack preconditions;
- minimal reproduction steps;
- relevant logs or traces with secrets removed;
- a suggested mitigation when known.

Never include API keys, passwords, access tokens, private keys, personal data, or production credentials in reports.

## Secret handling

Secrets must stay out of source control, issues, PRs, ExecutionTrace, client-visible errors, and normal application logs. Use environment variables or the configured secret/credential store.

When a secret is accidentally exposed, revoke or rotate it first and then document the incident without copying the secret into the repository.
