---
name: cloudru-management
description: Manage Cloud.ru Evolution infrastructure or IAM credentials through its APIs. Use for inventory, VM lifecycle, project access, and service-account key work; not for unrelated cloud providers.
---

# Cloud.ru Management

Use the Cloud.ru Public API with a service-account Key ID/Key Secret. Keep both
secrets server-side; never put them in source, browser storage, logs, traces,
issues, or chat output.

## Choose the interface

- For Cloud.ru Evolution resources, use the documented REST API. Obtain a
  short-lived bearer token from `https://iam.api.cloud.ru/api/v1/auth/token`,
  then call the service-specific endpoint. For example, Evolution Compute uses
  `https://compute.api.cloud.ru/api/v1/vms` with `project_id`.
- For static API-key and service-account operations in Alice Pro, use the
  existing backend modules `cloudru_iam.py`, `cloudru_iam_routes.py`, and
  `provider_key_rotation.py`. Preserve their backend-only secret handling and
  explicit confirmation requirements.
- Use the Cloud CLI only for the Cloud.ru Advanced platform and compatible
  IAM AK/SK credentials. Do not feed Evolution Public API Key ID/Key Secret
  pairs to `cloud configure init`.

## Discovery first

1. Verify credentials by requesting a token without displaying it.
2. Confirm the target project and run read-only discovery before planning a
   change. A direct project GET is useful when project-list pagination or role
   visibility is incomplete.
3. Report the resource names, IDs, current state, and expected impact. Do not
   infer a project, region, or resource identifier from a similarly named one.

## Changes and credentials

- Get explicit confirmation immediately before creating, resizing, starting,
  stopping, deleting, rotating, or changing access to a cloud resource.
- For a requested VM lifecycle action, show the selected VM and current state
  first. Treat deletion and public-network exposure as important actions.
- Prefer a dedicated service account with project-scoped roles. Grant the
  narrowest service roles that satisfy the request; project administrator is
  appropriate only when the user asks for broad project management.
- An API key or Key Secret disclosed in chat is compromised for operational
  purposes: recommend reissuing or rotating it, but never rotate or revoke it
  without explicit user direction.

## Alice Pro integration

- Put configuration in server-side environment variables or the encrypted
  credential store, never in `.env.example`, frontend JavaScript, or test
  fixtures.
- Preserve `ExecutionTrace` correlation while excluding token and secret
  values from trace payloads and error messages.
- Follow `docs/integrations/cloudru-iam-wizard.md` for Alice Pro IAM key
  issuance and `docs/provider-key-rotation.md` for Foundation Models key
  rotation.
