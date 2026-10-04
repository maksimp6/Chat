# CI environment cache experiment (#757 / #762)

Baseline head `2d7d870`: SQLite + coverage 24.01s, PostgreSQL 36.44s,
required feedback wall-clock 89s, required workflow runner-time 6.40 minutes.
The experiment does not close #757 or replace any required check.

The CI image and A/B setup arm pin Python 3.14.7, Node 22.22.2 and shared Python/npm dependencies.
It contains no application source, Chrome or Android SDK. The content tag hashes
only Dockerfile, requirements.txt, requirements-dev.txt and package.json.
Existing tags are retained; updates to moving upstream dependencies require an
explicit environment-input change. Record the registry digest with measurements.

L1: immutable GHCR content tag. L2: `type=gha,scope=alice-ci-image` layers.
L3: pip/npm BuildKit cache mounts. GHA exports layers, **not cache mounts**.
L3 probes reuse the same live builder after changing an install-layer input.
They do not establish persistence of downloaded packages across runner VMs.

Only the builder writes packages. Benchmark image consumers initially require
packages:read because newly created GHCR packages are private. If the package is
public, measure anonymous pulls and remove that permission in a later change.
The same-repository PR #762 guard admits this experimental builder without a
merge; fork PRs cannot execute it. Default required jobs retain their setup,
caches, permissions, full suites and coverage gates.

Three fresh-runner repetitions per A/B arm run the exact same full SQLite suite,
xdist flags and coverage collection. A installs via setup-python/setup-node plus
existing caches. B authenticates, pulls the content image, starts a container and
mounts the exact same checkout. B does not include Chrome; the full Application
job's browser tests stay in required CI and are outside this representative job.
A/B coverage artifacts are independent of required CI's quality gates.

Record runner queue separately from runner startup. Workflow created_at → job
started_at includes dependency waiting and queue, so it is not VM startup.
The GitHub "Set up job" step and log timestamps bound runner preparation.
Record action cache-restore substeps from timestamps in logs; action step totals
include setup plus restore and must not be relabelled as setup-only measurements.
Runner arrival and useful-test markers use fractional epoch seconds; step API
boundaries have one-second resolution. Keep subsecond evidence from logs.

Build scenarios on fresh runners: cold (`no-cache`), warm imported GHA layers,
source-only (hash must stay unchanged), pytest-xdist 3.8.0 → 3.7.0,
requests lower-bound → exact 2.34.2, Prettier 3.6.2 → 3.6.1. Variant builders publish immutable content tags without replacing the baseline
cache. Fresh consumer runners pull each variant and run the same source suite.
Registry pulls are measured separately from local image export/startup.
Cold means no build layers; upstream base-image registry/service caches are not
under this experiment's control. Build records expose CACHED/DONE per layer.

Report each trial's preparation, pull/startup, test time, job wall-clock and
runner-time in #757, with SHA, image tag/digest, cache outcomes and runner type.
Compare both required-gate wall-clock and runner-time before wider adoption;
experimental builder/A/B costs are reported separately from the required gate.
Do not adopt an image for a job when pull/startup loses to cached setup.
Small repeated improvements count; a single faster trial does not prove a win.

Chrome uses #763's existing `:buildcache` / `BUILDKIT_INLINE_CACHE` implementation.
Use cloudru-chrome.yml workflow_dispatch with lane=test, never change production
ancestry checks. Measure setup, Docker build, immutable push, cache refresh,
container start/readiness, seed, restart, verify separately. Production timings
are historical evidence, not permission to deploy production during experiments.
Use the existing registry cache for cold/warm/source/worker-JS/dependency trials;
do not add a second cache. A shared alice-base remains a later measured experiment.

SQLite xdist workers select a separate temporary default database before test
collection imports db/app modules. Explicit per-test database overrides remain
intact. PostgreSQL is serial; the hook rejects PostgreSQL xdist until separate
worker databases exist. Container startup trusts only the mounted checkout path
for Git, and jq is part of the shared system dependencies.

Chrome build-only probes use action=cache-benchmark, lane=test and cache_scenario
in cold/warm/python/worker-js/dependencies order, on separate dispatched runners.
They reuse #763's registry/cache refresh code and never create/start containers.
Cold disables build-layer reuse; dependency probe adds procps to the system layer.
Python-only changes are outside the worker Docker context. Variant tags identify
the exact source SHA and scenario; the existing buildcache tag is refreshed by
these probes, so rerun the warm baseline after dependency probes if comparing it.
Full deploy/readiness still needs an Object Storage tenant ID, supplied via the
existing workflow input. Build-only probes do not need that storage input.
