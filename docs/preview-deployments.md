# Preview deployments

Alice Pro previews run as isolated, short-lived Fly.io applications. They are intended for manual verification of a branch or pull request before merge.

## Required repository secrets

Configure these in **Settings → Secrets and variables → Actions**:

- `FLY_API_TOKEN`: a Fly.io token allowed to create, deploy, and destroy preview apps.
- `FLY_ORG`: the Fly.io organization slug where previews are created.

Never put Yandex, Supabase, production database, or signing credentials into the preview workflow.

## Deploy a branch

Open **Actions → Preview deployment → Run workflow** and enter a branch, tag, or commit in `ref`. The workflow creates an app named `alice-preview-<run-id>`, deploys it in the Frankfurt region, and performs an HTTPS health check against `/`.

## Pull requests

Opening, reopening, or updating a pull request automatically deploys its head commit to `alice-preview-<pull-request-number>`. The workflow writes the URL to the run summary and comments it on the pull request.

When the pull request is closed, the cleanup job destroys the corresponding preview app.

## Isolation and safety

- Preview instances use the `ALICE_PREVIEW=1` marker and a dedicated Fly.io app.
- The workflow does not run database migrations.
- Production credentials are not supplied by this workflow.
- Preview storage is ephemeral unless the application is explicitly changed to use an external service.
- The default lifetime is 24 hours for manually triggered previews; Fly.io auto-stop is enabled to reduce idle usage.

A preview is not a production environment. Do not enter real user data or production secrets into it.
