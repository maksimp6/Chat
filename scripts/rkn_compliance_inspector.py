#!/usr/bin/env python3
"""Build a conservative, evidence-backed RKN compliance manifest.

The inspector never converts missing evidence into a successful control.
Repository evidence is intentionally narrower than production observation:
runtime-only facts such as a live TLS certificate remain unknown until a
runtime probe supplies evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    target = ROOT / path
    return target.read_text(encoding="utf-8") if target.is_file() else ""


def _control(status: str, evidence: list[str], value=None) -> dict:
    result = {"status": status, "evidence": evidence}
    if value is not None:
        result["value"] = value
    return result


def inspect(root: Path = ROOT) -> dict:
    global ROOT
    old_root = ROOT
    ROOT = root
    try:
        ci = _read(".github/workflows/ci.yml")
        logging_doc = _read("deploy/cloudru/logging/README.md")
        services = _read("deploy/cloudru/services.md")
        requirements = _read("requirements.txt").lower()
        env_example = _read(".env.example").lower()

        backup_evidence = []
        if (root / "scripts/pg_backup.sh").is_file():
            backup_evidence.append("scripts/pg_backup.sh")
        if "pg_backup.sh verify" in ci:
            backup_evidence.append(".github/workflows/ci.yml: backup/restore verification")

        redaction_evidence = []
        if "redact" in logging_doc.lower():
            redaction_evidence.append("deploy/cloudru/logging/README.md: redaction contract")

        retention_evidence = []
        if "retention" in logging_doc.lower() or "срок хранения" in logging_doc.lower():
            retention_evidence.append("deploy/cloudru/logging/README.md: retention requirement")

        external = []
        corpus = "\n".join((services, requirements, env_example)).lower()
        for marker, name in (
            ("cloud.ru", "cloud.ru"),
            ("yandex", "yandex"),
            ("google", "google"),
            ("openai", "openai"),
            ("sentry", "sentry"),
            ("analytics", "analytics"),
        ):
            if marker in corpus:
                external.append(name)

        # No password hashing implementation is currently evidenced by the
        # dependency manifests. Authentication may be delegated, so this is
        # unknown rather than fail.
        password_known = any(name in requirements for name in ("argon2", "bcrypt", "passlib"))

        return {
            "version": 1,
            "controls": {
                "password_storage": _control(
                    "pass" if password_known else "unknown",
                    ["requirements.txt: password hashing dependency"]
                    if password_known
                    else [],
                ),
                "backup_restore": _control(
                    "pass" if len(backup_evidence) == 2 else "unknown",
                    backup_evidence,
                ),
                "log_redaction": _control(
                    "pass" if redaction_evidence else "unknown", redaction_evidence
                ),
                # A requirement in documentation is not proof of configured
                # runtime retention.
                "log_retention": _control("unknown", retention_evidence),
                # Repository config cannot prove the live endpoint certificate,
                # redirect or covered hostnames.
                "tls_public_endpoint": _control("unknown", []),
            },
            "external_services": sorted(set(external)),
            "evidence": sorted(
                set(backup_evidence + redaction_evidence + retention_evidence)
            ),
        }
    finally:
        ROOT = old_root


def link(actual: dict, declared: dict) -> list[str]:
    errors = []
    controls = actual.get("controls", {})
    for name in declared.get("required_controls", []):
        status = controls.get(name, {}).get("status", "unknown")
        if status != "pass":
            errors.append(f"required control {name!r} is {status!r}")

    allowed = set(declared.get("allowed_external_services", []))
    for service in actual.get("external_services", []):
        if service not in allowed:
            errors.append(f"undeclared external service: {service}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="compliance/rkn/actual-state.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    actual = inspect()
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.check:
        declared = json.loads(_read("compliance/rkn/declared-intent.json"))
        errors = link(actual, declared)
        if errors:
            for error in errors:
                print(f"RKN-COMPLIANCE: {error}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
