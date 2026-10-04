"""Config loading and validation for Alice Platform."""

import json
import re
from pathlib import Path
from typing import Any, Dict, Set

import yaml


class ConfigError(Exception):
    """Configuration validation error."""

    pass


def _is_secret_pattern(value: Any) -> bool:
    """Check if a value looks like a secret (base64, hex, PEM key, etc)."""
    if not isinstance(value, str):
        return False

    # PEM keys
    if "-----BEGIN" in value or "-----END" in value:
        return True

    # Long base64 or hex (32+ chars of base64/hex)
    if re.match(r"^[A-Za-z0-9+/=]{32,}$", value):
        return True
    if re.match(r"^[A-Fa-f0-9]{32,}$", value):
        return True

    return False


def _validate_no_plaintext_secrets(
    data: Dict[str, Any], path: str = "config", is_secrets_file: bool = False
) -> None:
    """Recursively check for plaintext secrets in config."""
    for key, value in data.items():
        if isinstance(value, dict):
            # Secret refs are OK: {"ref": "alice/prod/..."}
            if "ref" in value and isinstance(value.get("ref"), str):
                if not value["ref"].startswith("alice/"):
                    raise ConfigError(f"Invalid secret ref pattern at {path}.{key}: {value['ref']}")
                continue

            is_secrets_section = is_secrets_file or key == "secrets"
            _validate_no_plaintext_secrets(value, f"{path}.{key}", is_secrets_section)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    _validate_no_plaintext_secrets(item, f"{path}.{key}[{i}]", is_secrets_file)
        elif isinstance(value, str):
            # In secrets section, only refs are allowed
            if is_secrets_file and not value.startswith("alice/"):
                raise ConfigError(f"Plaintext secret detected at {path}.{key}")
            # Everywhere, check for secret patterns
            elif _is_secret_pattern(value):
                raise ConfigError(f"Plaintext secret detected at {path}.{key}")


def _has_cycle(
    graph: Dict[str, Set[str]], node: str, visited: Set[str], rec_stack: Set[str]
) -> bool:
    """Check if there's a cycle in the dependency graph."""
    visited.add(node)
    rec_stack.add(node)

    for neighbor in graph.get(node, set()):
        if neighbor not in visited:
            if _has_cycle(graph, neighbor, visited, rec_stack):
                return True
        elif neighbor in rec_stack:
            return True

    rec_stack.remove(node)
    return False


def _validate_no_cycles(services: Dict[str, Dict[str, Any]]) -> None:
    """Check for cycles in service dependencies."""
    # Build dependency graph
    graph: Dict[str, Set[str]] = {}
    for service_name, service_config in services.items():
        deps = service_config.get("depends_on", [])
        if not isinstance(deps, list):
            raise ConfigError(f"Service '{service_name}': depends_on must be a list")

        # Check all dependencies exist
        for dep in deps:
            if dep not in services:
                raise ConfigError(
                    f"Service '{service_name}' depends on nonexistent service '{dep}'"
                )

        graph[service_name] = set(deps)

    # Check for cycles
    visited: Set[str] = set()
    for node in graph:
        if node not in visited:
            if _has_cycle(graph, node, visited, set()):
                raise ConfigError(f"Circular dependency detected involving service '{node}'")


def _validate_schema(data: Dict[str, Any]) -> None:
    """Validate config against schema rules."""
    # Check for unknown fields at top level
    allowed_keys = {"services", "lanes", "domains", "storage", "secrets", "schema"}
    for key in data.keys():
        if key not in allowed_keys:
            raise ConfigError(f"Unknown field at top level: '{key}'")

    # Validate services structure
    services = data.get("services", {})
    if not isinstance(services, dict):
        raise ConfigError("'services' must be a dictionary")

    for service_name, service_config in services.items():
        if not isinstance(service_config, dict):
            raise ConfigError(f"Service '{service_name}' config must be a dictionary")

        # Check required fields
        if "type" not in service_config:
            raise ConfigError(f"Service '{service_name}' missing required field 'type'")

        # Check unknown fields in service - be permissive for service-specific config
        allowed_service_fields = {
            "type",
            "depends_on",
            "scale",
            "resources",
            "configuration",
            "client_id",
            "client_secret",
            "signing_key",
            "redirect_uris",
            "scopes",
            "owner_id",
            "secret_ref",
        }
        for key in service_config.keys():
            if key not in allowed_service_fields and not key.startswith("_"):
                raise ConfigError(f"Unknown field in service '{service_name}': '{key}'")


def validate_no_cycles(services: Dict[str, Dict[str, Any]]) -> None:
    """Exported function for testing."""
    _validate_no_cycles(services)


def validate_no_plaintext_secrets(data: Dict[str, Any]) -> None:
    """Exported function for testing."""
    _validate_no_plaintext_secrets(data)


def validate_schema(data: Dict[str, Any]) -> None:
    """Exported function for testing."""
    _validate_schema(data)


def validate_no_http_in_production(domains: Dict[str, Any], lanes: Dict[str, Any]) -> None:
    """Exported function for testing."""
    _validate_http_in_production(domains, lanes)


def validate_service_references(
    domains: Dict[str, Any], services: Dict[str, Any], lanes: Dict[str, Any]
) -> None:
    """Validate that all service references are valid."""
    # Check domains reference existing services
    for domain, domain_config in domains.items():
        if isinstance(domain_config, dict):
            service = domain_config.get("service")
            if service and service not in services:
                raise ConfigError(f"Domain '{domain}' references nonexistent service '{service}'")

    # Check all services are deployed in at least one lane
    deployed_services: Set[str] = set()
    for lane_name, lane_config in lanes.items():
        if isinstance(lane_config, dict):
            lane_services = lane_config.get("services", [])
            if isinstance(lane_services, list):
                deployed_services.update(lane_services)

    for service_name in services.keys():
        if service_name not in deployed_services:
            raise ConfigError(f"Service '{service_name}' not deployed in any lane")


def validate_http_in_production(domains: Dict[str, Any], lanes: Dict[str, Any]) -> None:
    """Reject HTTP in production lane."""
    is_production = "production" in lanes

    for domain, domain_config in domains.items():
        if isinstance(domain_config, dict):
            protocol = domain_config.get("protocol", "https")
            if is_production and protocol == "http":
                raise ConfigError(
                    f"Domain '{domain}' uses HTTP in production lane (https required)"
                )


def validate_lane_invariants(lanes: Dict[str, Dict[str, Any]]) -> None:
    """Validate lane-specific rules."""
    lane_secret_refs: Dict[str, Set[str]] = {}  # Track which lanes reference which secrets

    for lane_name, lane_config in lanes.items():
        if not isinstance(lane_config, dict):
            continue

        # Production must use GitHub sign_in
        if lane_name == "production":
            sign_in = lane_config.get("sign_in")
            if sign_in != "github":
                raise ConfigError(f"Production lane must use 'sign_in: github', got '{sign_in}'")

        # Collect all secret references in this lane
        lane_secret_refs[lane_name] = set()

        # Test lane cannot use GitHub secrets
        if lane_name == "test":
            for key, value in lane_config.items():
                if isinstance(value, str):
                    # Check for prod secrets in test lane
                    if value.startswith("alice/prod/"):
                        raise ConfigError(
                            f"Test lane cannot reference production secrets: {key} = {value}"
                        )
                    # Track secret references
                    if value.startswith("alice/"):
                        lane_secret_refs[lane_name].add(value)
        else:
            # For non-test lanes, still track secret references
            for key, value in lane_config.items():
                if isinstance(value, str) and value.startswith("alice/"):
                    lane_secret_refs[lane_name].add(value)

    # Check that lanes don't share secret references
    all_refs: Dict[str, list] = {}  # ref -> [lanes using it]
    for lane_name, refs in lane_secret_refs.items():
        for ref in refs:
            if ref not in all_refs:
                all_refs[ref] = []
            all_refs[ref].append(lane_name)

    # Flag any secret used by multiple lanes
    for ref, using_lanes in all_refs.items():
        if len(using_lanes) > 1:
            raise ConfigError(
                f"Secret reference '{ref}' is shared between lanes: {', '.join(using_lanes)}"
            )


def load_config(config_dir: Path) -> Dict[str, Any]:
    """Load and validate config from directory."""
    config_dir = Path(config_dir)

    # Load all YAML files
    data: Dict[str, Any] = {}

    # Load platform.yaml (required)
    platform_file = config_dir / "platform.yaml"
    if not platform_file.exists():
        raise ConfigError(f"Missing required file: platform.yaml")

    with open(platform_file) as f:
        platform_data = yaml.safe_load(f) or {}

    data.update(platform_data)

    # Load optional files
    for filename in [
        "domains.yaml",
        "storage.yaml",
        "secrets.yaml",
        "production.yaml",
        "test.yaml",
    ]:
        filepath = config_dir / filename
        if filepath.exists():
            with open(filepath) as f:
                file_data = yaml.safe_load(f) or {}

                # Merge appropriately
                if filename == "production.yaml" or filename == "test.yaml":
                    # Merge into lanes
                    if "lanes" not in data:
                        data["lanes"] = {}
                    lane_name = filename.replace(".yaml", "")
                    if lane_name in file_data.get("lanes", {}):
                        data["lanes"][lane_name] = file_data["lanes"][lane_name]
                else:
                    # Direct merge
                    if filename == "domains.yaml":
                        data["domains"] = file_data
                    elif filename == "storage.yaml":
                        data["storage"] = file_data
                    elif filename == "secrets.yaml":
                        data["secrets"] = file_data

    # Perform validation
    services = data.get("services", {})
    domains = data.get("domains", {})
    lanes = data.get("lanes", {})

    # Schema validation first
    _validate_schema(data)

    # Check for plaintext secrets
    _validate_no_plaintext_secrets(data)

    # Check for cycles in dependencies
    _validate_no_cycles(services)

    # Check service references
    validate_service_references(domains, services, lanes)

    # Check HTTP in production
    validate_http_in_production(domains, lanes)

    # Check lane invariants
    validate_lane_invariants(lanes)

    return data
