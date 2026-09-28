# Cloud.ru Container Apps deployment (baseline)

Status: baseline deploy path, not yet run against a live Cloud.ru project.
Issue: #427

Alice Pro runs as one Cloud.ru Evolution **Container Apps** service built from an
exact commit and pulled from **Artifact Registry** by digest. No VM or Kubernetes
cluster is created. Evolution has no separate Functions product for Docker
images, so Container Apps is the serverless runtime.

## Pieces

| File                                     | Role                                                                                                                                  |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `cloud/cloudru/registry_client.py`       | Registry list/create/delete (`ar.api.cloud.ru`), `docker login`/build/push, digest pinning                                            |
| `cloud/cloudru/container_apps_client.py` | Container service create/update/delete/start/stop/status (`containers.api.cloud.ru`), readiness wait, `/healthz` check, cost estimate |
| `scripts/cloudru_deploy.py`              | CLI: `deploy`, `status`, `delete --yes`, `estimate` (JSON output, no secret values)                                                   |
| `.github/workflows/cloudru-deploy.yml`   | Manual `workflow_dispatch` in the `cloudru` environment                                                                               |

Both clients reuse `CloudRuClient`, so requests are traced the same way as the
rest of the Cloud.ru provider. They authenticate with an IAM bearer token from
the service-account key pair and ignore `CLOUDRU_API_KEY` (Foundation Models).

## One-time setup

1. Create a service account in the target project with Artifact Registry
   (push) and Container Apps (admin) roles, and issue an access key.
2. In GitHub, create the `cloudru` environment with:
   - secrets `CLOUDRU_IAM_KEY_ID`, `CLOUDRU_IAM_KEY_SECRET`, `ALICE_SHORT_TOKEN`,
     and optionally `ALICE_PROVIDER_CREDENTIAL_KEY` and `ALICE_DATABASE_URL`;
   - variable `CLOUDRU_PROJECT_ID`, and optionally `CLOUDRU_REGISTRY_NAME`,
     `CLOUDRU_REPOSITORY_NAME`, `CLOUDRU_CONTAINER_NAME`, `CLOUDRU_CONTAINER_CPU`,
     `CLOUDRU_MIN_INSTANCES`, `CLOUDRU_MAX_INSTANCES`.
3. Run **Cloud.ru Container Apps deployment** with `action: estimate`, then
   `action: deploy`.

`deploy` creates the registry if missing, pushes `<registry>.cr.cloud.ru/alice-pro:<sha>`,
creates the service (or rolls out a new revision), waits until it runs the new
digest with a public URL, and requires `GET /healthz` to return 200.
If the new revision fails readiness or the health check, it restores the
previous revision's full configuration (image, env, scaling, resources) and
fails the run. Every action runs only on commits that are already on `master`,
checked before dependencies are installed, and secrets are scoped to the steps
that call Cloud.ru (`estimate` gets none). The deploy script builds from a
`git archive` export of the `--tag` commit, so untracked and ignored files never
reach the image. It refuses to deploy without the short-token gate, keeps the
registry credential in a throwaway Docker config, and fails closed if the
registry reports no image digest.

## Cost

Defaults: 0.5 vCPU / 1 GiB, scale to zero (`CLOUDRU_MIN_INSTANCES=0`), at most
one instance. With no warm instance there is no fixed floor: you pay only for
the time an instance is awake, at the Container Services pay-as-you-go example
prices (1.8905 RUB per vCPU·h, 1.257 RUB per GB·h) after the monthly free tier
(25 vCPU·h, 50 GB·h). That free tier covers roughly 50 awake hours a month at
this size. Keeping one instance warm (`CLOUDRU_MIN_INSTANCES=1`) costs about
**1,500 RUB/month**. `scripts/cloudru_deploy.py estimate` prints the floor for
the configured size. Check the live tariff before provisioning:
https://cloud.ru/docs/container-apps-evolution/ug/topics/pricing__container-services

The first request after idle pays a cold start.

## Known limitations

- **State is ephemeral without Postgres.** SQLite lives in the container
  filesystem and is lost whenever the instance sleeps or a new revision starts.
  Store `ALICE_DATABASE_URL` (Cloud.ru Managed PostgreSQL) as a secret; the
  deploy passes it through and the existing Postgres backend takes over.
- **In-process state is lost on sleep.** Branch environment runtimes (git
  worktrees under `.alice-environments`), voice sessions, and uploaded files
  live in the container. Keep `CLOUDRU_MAX_INSTANCES=1` until they move to
  Postgres/Object Storage, or requests may land on an instance without them.
- **Scale to zero stops background work.** Background threads and SSH runtime
  containers stop when the instance sleeps; they need to move to request-driven
  work or Container Apps jobs.
- **App secrets are plain container env vars**, visible to anyone with Container
  Apps read access in the project. Move them to a secret store as a follow-up.
- **API field names are unconfirmed.** The rendered API reference is not
  machine-readable; paths and bodies were cross-checked against a working
  community client. Confirm them on the first live deploy.
- **Readiness does not track revisions yet.** The deploy waits for the new image
  digest and a running status. A rollout that keeps the same digest (config
  only) can pass that check while the old revision still serves, so the health
  check may hit it. Pin the check to the rollout's revision once the first live
  deploy confirms which field carries it.
