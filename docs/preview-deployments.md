# Preview deployments

Alice Pro previews run on the project VPS as isolated Docker containers behind one Traefik reverse proxy. Each pull request receives a path-based URL such as:

`http://SERVER_IP/preview/pr-203/`

The application container never publishes a host port. Traefik is the only container bound to port 80.

## Required repository secrets

Configure these in **Settings → Secrets and variables → Actions**:

- `PREVIEW_SSH_HOST`: VPS hostname or IP used for SSH.
- `PREVIEW_SSH_USER`: deployment user on the VPS.
- `PREVIEW_SSH_PRIVATE_KEY`: private SSH key for that deployment user.
- `PREVIEW_SSH_KNOWN_HOSTS`: the exact known-hosts entry for the VPS.
- `PREVIEW_PUBLIC_BASE_URL`: public origin, for example `http://203.0.113.10`.

Optional repository variables:

- `PREVIEW_SSH_PORT`: SSH port, default `22`.
- `PREVIEW_SERVER_BASE_DIR`: server working directory, default `/opt/alice-preview`.
- `PREVIEW_TTL_HOURS`: preview lifetime, default `24`.

Do not place Yandex, Supabase, production database, or Android signing credentials in this workflow.

## Pull requests

Opening, reopening, or updating a pull request automatically deploys its head commit as:

`/preview/pr-<number>/`

The workflow:

1. checks out the exact PR commit;
2. builds a source archive on GitHub Actions;
3. uploads it over SSH;
4. builds the Docker image on the VPS;
5. starts/replaces the PR container;
6. registers the route with Traefik;
7. checks `/healthz` locally through Traefik and then checks the public URL;
8. posts the preview URL to the workflow summary and PR conversation.

When the pull request is closed, the corresponding container and image are removed.

## Manual branch previews

Run **Actions → Preview deployment → Run workflow** and set `ref` to a branch, tag, or commit.

Manual previews use:

`/preview/branch-<ref-slug>/`

Re-running the same ref replaces the previous preview for that key.

## VPS runtime

The workflow installs a single Traefik container named `alice-preview-traefik` when it is not already present.

Traefik uses:

- Docker provider;
- `alice-preview` Docker network;
- port `80`;
- `Path(...)` / `PathPrefix(...)` routers per preview;
- StripPrefix middleware so Flask continues receiving its normal `/` and `/api/*` routes.

Preview containers receive:

- `ALICE_PREVIEW=1`;
- `ALICE_PREVIEW_BASE_PATH=/preview/<key>`;
- `HOST=0.0.0.0`;
- `PORT=8080`.

The frontend uses the configured base path for local static assets, API requests, and Server-Sent Events. The application also exposes `GET /healthz`.

## Isolation and safety

- Each preview has its own Docker container, image tag, filesystem, and SQLite/runtime state.
- Preview containers are not attached to the production network and do not expose host ports.
- The workflow does not run database migrations.
- Production credentials are not supplied.
- The Docker build excludes Git metadata, environment files, local logs, and local database files.
- Preview containers are labeled with an expiry timestamp.
- An hourly scheduled workflow removes expired previews.
- Closing a PR removes its preview immediately.

A preview is not production. Do not enter real user data or production secrets into it.

## First VPS setup

The deployment account needs permission to run Docker commands. The VPS must have Docker installed and port 80 available.

No Nginx or Traefik installation is required beforehand. The workflow starts its own Traefik container automatically.

The first deployment creates:

`/opt/alice-preview/server.sh`

or the configured `PREVIEW_SERVER_BASE_DIR`, plus the `incoming/` and `previews/` directories.

## HTTPS

The initial path-based setup intentionally uses HTTP because it works directly with a VPS IP address. Add a domain and TLS configuration later when the public hostname is available. The preview routing itself does not need to change.
