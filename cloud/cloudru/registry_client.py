"""Cloud.ru Evolution Artifact Registry client.

Control-plane calls (list/create/delete registries) go through ``CloudRuClient``
so they share its trace-safe logging. Image build/push uses the local Docker CLI
with the IAM key pair passed on stdin, never on the command line.

API paths follow the Artifact Registry public API (``https://ar.api.cloud.ru``);
confirm them against the live reference on the first real deploy:
https://cloud.ru/docs/artifact-registry-evolution/ug/index
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
import tempfile
import time
from typing import Any, Callable

from cloud.base import CloudProviderError
from cloud.cloudru.client import CloudRuClient
from cloudru_iam import CloudRuIamClient


SERVICE = "artifact_registry"
DEFAULT_REGISTRY_DOMAIN = "cr.cloud.ru"
ALLOWED_REGISTRY_DOMAINS = frozenset({DEFAULT_REGISTRY_DOMAIN})
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
# Fixed tag holding the inline BuildKit layer cache for the next CI build.
CACHE_TAG = "buildcache"

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class ImageRef:
    registry_host: str
    repository: str
    tag: str
    digest: str | None = None

    @property
    def tagged(self) -> str:
        return f"{self.registry_host}/{self.repository}:{self.tag}"

    @property
    def pinned(self) -> str:
        """Immutable reference; deploys should use this, not the mutable tag."""
        if not self.digest:
            return self.tagged
        return f"{self.registry_host}/{self.repository}@{self.digest}"


def validate_name(value: str, field: str) -> str:
    value = str(value or "").strip()
    if not _NAME_RE.match(value):
        raise CloudProviderError(
            f"{field} must be lowercase letters, digits and dashes", code="validation_error"
        )
    return value


def validate_registry_domain(value: str) -> str:
    """Allow only the official Cloud.ru registry domain before using IAM auth."""
    domain = str(value or "").strip().lower()
    if domain not in ALLOWED_REGISTRY_DOMAINS:
        allowed = ", ".join(sorted(ALLOWED_REGISTRY_DOMAINS))
        raise CloudProviderError(
            f"CLOUDRU_REGISTRY_DOMAIN must be one of: {allowed}",
            code="validation_error",
        )
    return domain


class CloudRuRegistryClient:
    def __init__(
        self,
        *,
        project_id: str | None = None,
        client: CloudRuClient | None = None,
        iam_client: CloudRuIamClient | None = None,
        registry_domain: str | None = None,
        runner: Runner = subprocess.run,
    ) -> None:
        self.iam_client = iam_client or CloudRuIamClient()
        self.client = client or CloudRuClient(iam_client=self.iam_client, api_key_auth=False)
        self.project_id = (project_id or os.getenv("CLOUDRU_PROJECT_ID", "")).strip()
        configured_domain = (
            registry_domain or os.getenv("CLOUDRU_REGISTRY_DOMAIN") or DEFAULT_REGISTRY_DOMAIN
        )
        self.registry_domain = validate_registry_domain(configured_domain)
        self._run = runner

    def _require_project(self) -> str:
        if not self.project_id:
            raise CloudProviderError("CLOUDRU_PROJECT_ID is required", code="validation_error")
        return self.project_id

    def registry_host(self, registry_name: str) -> str:
        domain = validate_registry_domain(self.registry_domain)
        return f"{validate_name(registry_name, 'registry_name')}.{domain}"

    # Control plane -----------------------------------------------------------

    def list_registries(self) -> list[dict[str, Any]]:
        payload = self.client.request(
            SERVICE, "GET", "/v1/registries", params={"projectId": self._require_project()}
        )
        items = payload.get("registries") or payload.get("items") or []
        if not isinstance(items, list):
            raise CloudProviderError("registry list payload is invalid", code="invalid_response")
        return [item for item in items if isinstance(item, dict)]

    def get_registry(self, registry_name: str) -> dict[str, Any] | None:
        name = validate_name(registry_name, "registry_name")
        return next((r for r in self.list_registries() if r.get("name") == name), None)

    def ensure_registry(self, registry_name: str, *, is_public: bool = False) -> dict[str, Any]:
        """Return the registry, creating a private Docker registry if it is missing."""
        if is_public:
            raise CloudProviderError(
                "deployment requires a private registry", code="validation_error"
            )
        existing = self.get_registry(registry_name)
        if existing is not None:
            if existing.get("isPublic") is not False or existing.get("registryType") != "DOCKER":
                raise CloudProviderError(
                    "existing registry must explicitly be private and type DOCKER",
                    code="validation_error",
                )
            return existing
        return self.client.request(
            SERVICE,
            "POST",
            "/v1/registries",
            json_body={
                "projectId": self._require_project(),
                "name": validate_name(registry_name, "registry_name"),
                "isPublic": bool(is_public),
                "registryType": "DOCKER",
            },
        )

    def delete_registry(self, registry_id: str) -> dict[str, Any]:
        registry_id = str(registry_id or "").strip()
        if not registry_id or "/" in registry_id:
            raise CloudProviderError("registry_id is required", code="validation_error")
        return self.client.request(SERVICE, "DELETE", f"/v1/registries/{registry_id}")

    # Data plane (Docker CLI) -------------------------------------------------

    def docker_login(self, registry_name: str, *, env: dict[str, str] | None = None) -> str:
        host = self.registry_host(registry_name)
        if not self.iam_client.key_id or not self.iam_client.key_secret:
            raise CloudProviderError(
                "CLOUDRU_IAM_KEY_ID and CLOUDRU_IAM_KEY_SECRET are required",
                code="auth_not_configured",
            )
        result = None
        for attempt in range(2):
            try:
                result = self._run(
                    ["docker", "login", host, "--username", self.iam_client.key_id, "--password-stdin"],
                    input=self.iam_client.key_secret,
                    text=True,
                    capture_output=True,
                    check=False,
                    env=env,
                    timeout=5,
                )
            except subprocess.TimeoutExpired:
                if attempt == 0:
                    continue
                raise CloudProviderError(
                    f"docker login to {host} timed out",
                    code="authorization_failed",
                ) from None
            if result.returncode == 0:
                return host
            stderr = (result.stderr or "").strip()
            transient = any(token in stderr.lower() for token in ("eof", "connection reset", "tls handshake timeout"))
            if attempt == 0 and transient:
                continue
            break
        # Docker's stderr never contains the password, but keep it short anyway.
        raise CloudProviderError(
            f"docker login to {host} failed: {((result.stderr if result else '') or '').strip()[:300]}",
            code="authorization_failed",
        )

    def build_and_push_fast(
        self,
        *,
        registry_name: str,
        repository: str,
        tag: str,
        context_dir: str = ".",
        dockerfile: str = "Dockerfile",
        platform: str = "linux/amd64",
        max_seconds: float = 15.0,
        build_args: dict[str, str] | None = None,
        registry_cache: bool = True,
    ) -> ImageRef:
        """Build+push with BuildKit registry cache and no local cache-image transfer."""
        repository = validate_name(repository, "repository")
        if not _TAG_RE.match(tag or ""):
            raise CloudProviderError("tag is invalid", code="validation_error")
        with tempfile.TemporaryDirectory(prefix="alice-docker-") as docker_config:
            env = {**os.environ, "DOCKER_CONFIG": docker_config}
            host = self.docker_login(registry_name, env=env)
            if registry_cache:
                builder = "alice-registry-fast"
                inspect = self._run(
                    ["docker", "buildx", "inspect", builder],
                    text=True, capture_output=True, check=False, env=env,
                )
                if inspect.returncode != 0:
                    create = self._run(
                        ["docker", "buildx", "create", "--name", builder, "--driver", "docker-container", "--use"],
                        text=True, capture_output=True, check=False, env=env,
                    )
                    if create.returncode != 0:
                        raise CloudProviderError(
                            f"docker buildx builder create failed: {(create.stderr or '').strip()[-500:]}",
                            code="docker_error",
                        )
                else:
                    use = self._run(
                        ["docker", "buildx", "use", builder],
                        text=True, capture_output=True, check=False, env=env,
                    )
                    if use.returncode != 0:
                        raise CloudProviderError("docker buildx builder unavailable", code="docker_error")
            else:
                use = self._run(
                    ["docker", "buildx", "use", "default"],
                    text=True, capture_output=True, check=False, env=env,
                )
                if use.returncode != 0:
                    raise CloudProviderError("default buildx builder unavailable", code="docker_error")
            ref = ImageRef(host, repository, tag)
            cache = ImageRef(host, repository, CACHE_TAG).tagged
            build_argv = [
                "docker",
                "buildx",
                "build",
                "--platform",
                platform,
            ]
            if registry_cache:
                build_argv.extend(
                    [
                        "--cache-from",
                        f"type=registry,ref={cache}",
                        "--cache-to",
                        f"type=registry,ref={cache},mode=max",
                    ]
                )
            for name, value in sorted((build_args or {}).items()):
                if not name or not value or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
                    raise CloudProviderError("build arg is invalid", code="validation_error")
                build_argv.extend(["--build-arg", f"{name}={value}"])
            with tempfile.NamedTemporaryFile(prefix="buildx-metadata-", suffix=".json") as metadata:
                started = time.perf_counter()
                try:
                    result = self._run(
                        [
                            *build_argv,
                            "--metadata-file",
                            metadata.name,
                            "-f",
                            dockerfile,
                            "-t",
                            ref.tagged,
                            "--push",
                            context_dir,
                        ],
                        text=True,
                        capture_output=True,
                        check=False,
                        env=env,
                        timeout=max_seconds,
                    )
                except subprocess.TimeoutExpired as exc:
                    seconds = time.perf_counter() - started
                    print(
                        json.dumps(
                            {
                                "stage": "registry_build_push_fast",
                                "seconds": seconds,
                                "returncode": 124,
                            }
                        ),
                        flush=True,
                    )
                    raise CloudProviderError(
                        f"docker buildx exceeded {max_seconds}s budget",
                        code="build_time_budget_exceeded",
                    ) from exc
                seconds = time.perf_counter() - started
                print(
                    json.dumps(
                        {
                            "stage": "registry_build_push_fast",
                            "seconds": seconds,
                            "returncode": result.returncode,
                        }
                    ),
                    flush=True,
                )
                if result.returncode != 0:
                    raise CloudProviderError(
                        f"docker buildx failed: {(result.stderr or '').strip()[-500:]}",
                        code="docker_error",
                    )
                if seconds > max_seconds:
                    raise CloudProviderError(
                        f"docker buildx exceeded {max_seconds}s budget",
                        code="build_time_budget_exceeded",
                    )
                try:
                    metadata.seek(0)
                    data = json.load(metadata)
                except (OSError, ValueError, TypeError):
                    data = {}
                digest_value = data.get("containerimage.digest") if isinstance(data, dict) else None
                if not isinstance(digest_value, str) or not _DIGEST_RE.fullmatch(digest_value):
                    matches = _DIGEST_RE.findall((result.stdout or "") + "\n" + (result.stderr or ""))
                    digest_value = matches[-1] if matches else None
                if not digest_value:
                    raise CloudProviderError(
                        "docker buildx reported no image digest",
                        code="docker_error",
                    )
                return ImageRef(host, repository, tag, digest_value)


    def build_and_push(
        self,
        *,
        registry_name: str,
        repository: str,
        tag: str,
        context_dir: str = ".",
        dockerfile: str = "Dockerfile",
        platform: str = "linux/amd64",
    ) -> ImageRef:
        repository = validate_name(repository, "repository")
        if not _TAG_RE.match(tag or ""):
            raise CloudProviderError("tag is invalid", code="validation_error")
        # A throwaway Docker config keeps the registry credential off disk after the push.
        with tempfile.TemporaryDirectory(prefix="alice-docker-") as docker_config:
            env = {**os.environ, "DOCKER_CONFIG": docker_config}
            return self._build_and_push(
                registry_name, repository, tag, context_dir, dockerfile, platform, env
            )

    def _build_and_push(
        self,
        registry_name: str,
        repository: str,
        tag: str,
        context_dir: str,
        dockerfile: str,
        platform: str,
        env: dict[str, str],
    ) -> ImageRef:
        host = self.docker_login(registry_name, env=env)
        ref = ImageRef(host, repository, tag)
        # CI runners start empty, so layers are reused through an inline BuildKit
        # cache kept in the same private registry under a fixed tag.
        cache = ImageRef(host, repository, CACHE_TAG).tagged
        # Hosted runners start with an empty local image store. Classic
        # docker build --cache-from can only reuse the registry cache after the
        # cache image has been pulled locally. A cold/missing cache is harmless.
        started = time.perf_counter()
        cache_pull = self._run(
            ["docker", "pull", cache], text=True, capture_output=True, check=False, env=env
        )
        print(
            json.dumps(
                {
                    "stage": "registry_cache_pull",
                    "seconds": time.perf_counter() - started,
                    "returncode": cache_pull.returncode,
                }
            ),
            flush=True,
        )
        for argv in (
            [
                "docker",
                "build",
                "--platform",
                platform,
                "--cache-from",
                cache,
                "--build-arg",
                "BUILDKIT_INLINE_CACHE=1",
                "-f",
                dockerfile,
                "-t",
                ref.tagged,
                "-t",
                cache,
                context_dir,
            ],
            ["docker", "push", ref.tagged],
        ):
            started = time.perf_counter()
            result = self._run(argv, text=True, capture_output=True, check=False, env=env)
            print(
                json.dumps(
                    {
                        "stage": "registry_" + argv[1],
                        "seconds": time.perf_counter() - started,
                        "returncode": result.returncode,
                        "cached_steps": len(
                            re.findall(
                                r"(?m)^#\d+ CACHED", (result.stdout or "") + (result.stderr or "")
                            )
                        ),
                    }
                ),
                flush=True,
            )
            if result.returncode != 0:
                raise CloudProviderError(
                    f"{argv[1]} failed: {(result.stderr or '').strip()[-500:]}",
                    code="docker_error",
                )
            if argv[1] == "push":
                match = _DIGEST_RE.search(result.stdout or "")
                if not match:
                    # Deploys must pin a digest; never fall back to the mutable tag.
                    raise CloudProviderError(
                        f"docker push of {ref.tagged} reported no sha256 digest",
                        code="docker_error",
                    )
                ref = ImageRef(host, repository, tag, match.group(0))
        # The cache only speeds up the next build; failing to refresh it must not
        # fail a deploy whose pinned image is already pushed.
        started = time.perf_counter()
        refresh = self._run(
            ["docker", "push", cache], text=True, capture_output=True, check=False, env=env
        )
        print(
            json.dumps(
                {
                    "stage": "registry_cache_refresh",
                    "seconds": time.perf_counter() - started,
                    "returncode": refresh.returncode,
                }
            ),
            flush=True,
        )
        return ref
