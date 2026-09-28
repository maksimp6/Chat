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
import os
import re
import subprocess
from typing import Any, Callable

from cloud.base import CloudProviderError
from cloud.cloudru.client import CloudRuClient
from cloudru_iam import CloudRuIamClient


SERVICE = "artifact_registry"
DEFAULT_REGISTRY_DOMAIN = "cr.cloud.ru"
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")

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


def _validate_name(value: str, field: str) -> str:
    value = str(value or "").strip()
    if not _NAME_RE.match(value):
        raise CloudProviderError(
            f"{field} must be lowercase letters, digits and dashes", code="validation_error"
        )
    return value


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
        self.registry_domain = (
            registry_domain or os.getenv("CLOUDRU_REGISTRY_DOMAIN") or DEFAULT_REGISTRY_DOMAIN
        ).strip()
        self._run = runner

    def _require_project(self) -> str:
        if not self.project_id:
            raise CloudProviderError("CLOUDRU_PROJECT_ID is required", code="validation_error")
        return self.project_id

    def registry_host(self, registry_name: str) -> str:
        return f"{_validate_name(registry_name, 'registry_name')}.{self.registry_domain}"

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
        name = _validate_name(registry_name, "registry_name")
        return next((r for r in self.list_registries() if r.get("name") == name), None)

    def ensure_registry(self, registry_name: str, *, is_public: bool = False) -> dict[str, Any]:
        """Return the registry, creating a private Docker registry if it is missing."""
        existing = self.get_registry(registry_name)
        if existing is not None:
            return existing
        return self.client.request(
            SERVICE,
            "POST",
            "/v1/registries",
            json_body={
                "projectId": self._require_project(),
                "name": _validate_name(registry_name, "registry_name"),
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

    def docker_login(self, registry_name: str) -> str:
        host = self.registry_host(registry_name)
        if not self.iam_client.key_id or not self.iam_client.key_secret:
            raise CloudProviderError(
                "CLOUDRU_IAM_KEY_ID and CLOUDRU_IAM_KEY_SECRET are required",
                code="auth_not_configured",
            )
        result = self._run(
            ["docker", "login", host, "--username", self.iam_client.key_id, "--password-stdin"],
            input=self.iam_client.key_secret,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            # Docker's stderr never contains the password, but keep it short anyway.
            raise CloudProviderError(
                f"docker login to {host} failed: {(result.stderr or '').strip()[:300]}",
                code="authorization_failed",
            )
        return host

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
        repository = _validate_name(repository, "repository")
        if not _TAG_RE.match(tag or ""):
            raise CloudProviderError("tag is invalid", code="validation_error")
        host = self.docker_login(registry_name)
        ref = ImageRef(host, repository, tag)
        for argv in (
            [
                "docker",
                "build",
                "--platform",
                platform,
                "-f",
                dockerfile,
                "-t",
                ref.tagged,
                context_dir,
            ],
            ["docker", "push", ref.tagged],
        ):
            result = self._run(argv, text=True, capture_output=True, check=False)
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
        return ref
