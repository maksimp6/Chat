#!/usr/bin/env python3
"""Read-only Cloud.ru check: proves the keys work and lists what exists.

Nothing is created, changed or deleted, and no secret value is ever read or printed.
Output is built from allow-listed fields (names, statuses, sizes, image digests,
environment variable *names*), so provider responses cannot leak values.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.base import CloudProviderError  # noqa: E402
from cloud.cloudru.container_apps_client import CloudRuContainerAppsClient  # noqa: E402
from cloudru_iam import CloudRuIamClient  # noqa: E402
from cloud.cloudru.registry_client import CloudRuRegistryClient  # noqa: E402
from cloud.cloudru.secret_management import CloudRuSecretManagementClient  # noqa: E402
from scripts.cloudru_browser_probe import registry_inventory  # noqa: E402

CHECKS = ("auth", "containers", "registries", "secrets")
SAFE_ERRORS = frozenset(
    {
        "validation_error",
        "auth_not_configured",
        "authorization_failed",
        "auth_failed",
        "provider_http_error",
        "invalid_response",
        "unsupported_capability",
    }
)


def expand(selection):
    if selection == "all":
        return list(CHECKS)
    return [selection]


def text(value):
    return value if isinstance(value, str) else None


def containers(apps):
    result = []
    for item in apps.list():
        if not isinstance(item, dict):
            result.append({"name": None})
            continue
        template = item.get("template") if isinstance(item.get("template"), dict) else {}
        first = (template.get("containers") or [{}])[0]
        first = first if isinstance(first, dict) else {}
        scaling = template.get("scaling") if isinstance(template.get("scaling"), dict) else {}
        resources = first.get("resources") if isinstance(first.get("resources"), dict) else {}
        image = text(first.get("image"))
        ingress = (item.get("configuration") or {}).get("ingress") or {}
        result.append(
            {
                "name": text(item.get("name")),
                "status": text(item.get("status")),
                "cpu": text(resources.get("cpu")),
                "memory": text(resources.get("memory")),
                "min_instances": scaling.get("minInstanceCount"),
                "max_instances": scaling.get("maxInstanceCount"),
                "public": ingress.get("publiclyAccessible"),
                "digest": image.split("@", 1)[1] if image and "@" in image else None,
                # Names only: the values may be secrets.
                "env_names": sorted(
                    entry["name"]
                    for entry in first.get("env") or []
                    if isinstance(entry, dict) and isinstance(entry.get("name"), str)
                ),
            }
        )
    return result


def registries(registry):
    return [
        {
            "name": text(item.get("name")),
            "status": text(item.get("status")),
            "public": item.get("isPublic"),
        }
        for item in registry_inventory(registry)
    ]


def secret_versions(client, secret_id):
    # Metadata only; the payload endpoint is never called.
    versions = client.list_versions(secret_id)
    return {
        "secret_id_set": True,
        "versions": [
            {key: text(version.get(key)) for key in ("id", "status", "created_at")}
            for version in versions
        ],
    }


def error_result(exc):
    code = getattr(exc, "code", None)
    status = getattr(exc, "http_status", None)
    return {
        "ok": False,
        "error": code if code in SAFE_ERRORS else "check_failed",
        "http_status": status if type(status) is int and 100 <= status <= 599 else None,
    }


DEFAULT_FACTORIES = {
    "auth": lambda env: CloudRuIamClient(),
    "containers": lambda env: CloudRuContainerAppsClient(project_id=env["CLOUDRU_PROJECT_ID"]),
    "registries": lambda env: CloudRuRegistryClient(),
    "secrets": lambda env: CloudRuSecretManagementClient(),
}


def run(names, env=os.environ, factories=None):
    factories = {**DEFAULT_FACTORIES, **(factories or {})}
    report = {}
    for name in names:
        if name not in CHECKS:
            raise ValueError(f"unknown check: {name}")
        try:
            if name == "secrets":
                if not env.get("CHECK_SECRET_ID"):
                    report[name] = {"ok": False, "skipped": "secret_id_not_provided"}
                    continue
                if not (
                    env.get("CLOUDRU_SECRET_MANAGEMENT_KEY_ID")
                    and env.get("CLOUDRU_SECRET_MANAGEMENT_KEY_SECRET")
                ):
                    report[name] = {
                        "ok": False,
                        "skipped": "secret_management_credentials_not_configured",
                    }
                    continue
            client = factories[name](env)
            if name == "auth":
                client._token()  # noqa: SLF001 - exchanging keys for a token is the check
                report[name] = {"ok": True}
            elif name == "containers":
                report[name] = {"ok": True, "data": containers(client)}
            elif name == "registries":
                report[name] = {"ok": True, "data": registries(client)}
            else:
                report[name] = {"ok": True, "data": secret_versions(client, env["CHECK_SECRET_ID"])}
        except Exception as exc:  # one failing check must not hide the others
            report[name] = error_result(exc)
    return report


def markdown(report):
    lines = ["## Cloud.ru check (read-only)\n"]
    for name, result in report.items():
        if result.get("ok"):
            lines.append(f"- **{name}**: ok")
            for row in result.get("data") or []:
                if isinstance(row, dict) and row.get("name"):
                    lines.append(f"  - `{row['name']}` {row.get('status') or ''}".rstrip())
        elif "skipped" in result:
            lines.append(f"- **{name}**: skipped ({result['skipped']})")
        else:
            lines.append(
                f"- **{name}**: failed ({result.get('error')}, HTTP {result.get('http_status')})"
            )
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=("all", *CHECKS))
    args = parser.parse_args(argv)
    report = run(expand(args.check))
    print(json.dumps(report, sort_keys=True), flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(markdown(report))
    return 0 if all(r.get("ok") or "skipped" in r for r in report.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
