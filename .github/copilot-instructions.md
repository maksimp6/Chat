# Copilot instructions

- Read `AGENTS.md` and the owner's current instruction for delivery and testing.
- `develop` is the integration branch; `master` requires separate explicit authorization.
- Code tests run only on a separate owner request inside GitHub Actions runner.
  Otherwise report `NOT_RUN`; never imitate the runner environment.
- Removed CI, lint, type, naming and layout gates are not required checks.
- Report concrete changes, exact SHA and validation actually performed.
- Stage acceptance (`OK`) does not authorize merge.
- Never commit secrets or add CDN-hosted UI dependencies.
