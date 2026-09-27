# Host-managed preview validation

Preview validation uses the running Alice Pro host. It does **not** build or start a
per-preview container, Flask subprocess, localhost proxy, or runtime port.

The GitHub workflow checks out and tests the exact requested commit, then follows
this lifecycle:

1. authenticate to the Alice Pro host;
2. create an environment for the branch and exact commit SHA;
3. start the host-managed runtime through `EnvironmentManager`;
4. exercise `/environments/<environment_id>/healthz` through the environment gateway;
5. emit non-secret commit/environment evidence;
6. delete the environment from an `EXIT` trap, including after validation failure or cancellation.

`EnvironmentManager` owns the detached source worktree, runtime-local data root,
`RuntimeDispatcher` registration, managed runtime thread, and deterministic
cleanup. The gateway streams responses through its bounded in-process queue.

## Required Actions secrets

- `ALICE_ENVIRONMENT_HOST_URL`: the running host origin. When short-token path
  authentication is enabled, include the token prefix in this secret. It must not
  be printed or placed in a pull-request comment.
- `ALICE_ENVIRONMENT_API_TOKEN`: optional bearer credential for deployments whose
  host authentication layer accepts one.

No production database, provider, tool, Android signing, SSH, Docker, or runtime
credentials are supplied to preview revision code.

## Dependency and rollout

This workflow is the focused successor to the old container deployment from
#341. It must not be enabled as a required deployment until the owner
authorization work in #347 and the constrained exact-revision `RuntimeLoader` in
#349 have merged. In particular, the loader—not CI and not a global `sys.path`
mutation—must attach `alice_runtime.py` from the verified detached commit before
the gateway can claim revision-specific behavior.

Until those dependencies land, the workflow documents and tests the host API
contract but remains gated by the `ALICE_HOST_PREVIEW_ENABLED` repository
variable. Set it to `true` only after both dependencies are deployed. Threads
are execution units for trusted Alice Pro code, not a sandbox for hostile Python.
