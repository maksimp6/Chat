"""Offline validation of prepared Ultra profiles; no credentials or network IO."""

from __future__ import annotations

import argparse
import ast
import json
from copy import deepcopy
from datetime import date
from pathlib import Path


class ProfileError(ValueError):
    """A prepared profile cannot safely supply an executable request."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def read_registry(path):
    """Read literal model declarations without importing core.config or .env."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    declarations = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "TEXT_MODELS",
                    "VOICE_MODELS",
                }:
                    declarations[target.id] = ast.literal_eval(node.value)
    if set(declarations) != {"TEXT_MODELS", "VOICE_MODELS"}:
        raise ProfileError("model declarations missing")
    if set(declarations["TEXT_MODELS"]) & set(declarations["VOICE_MODELS"]):
        raise ProfileError("duplicate registry model key")
    return {**declarations["TEXT_MODELS"], **declarations["VOICE_MODELS"]}


def _require(condition, message):
    if not condition:
        raise ProfileError(message)


def validate_catalog(catalog, registry, root):
    """Validate artifact coverage and fail-closed parameter contracts."""
    _require(catalog.get("schema_version") == 1, "unsupported schema")
    profiles = catalog["profiles"]
    _require(set(profiles) == set(registry), "registry/profile coverage mismatch")
    _require(
        len(profiles) == catalog["registry"]["expected_model_count"],
        "model count mismatch",
    )
    policy = catalog["policy"]
    _require(policy["automatic_generation"] is False, "automatic generation forbidden")
    _require(policy["model_fallback"] is None, "model fallback forbidden")
    _require(policy["parameter_downgrade"] is None, "parameter downgrade forbidden")
    _require(policy["automatic_post_retries"] == 0, "POST retries forbidden")
    _require(policy["full_initial_context"] is True, "complete context required")
    _require(
        policy["requires_explicit_run_scope_and_budget"] is True,
        "run scope and budget required",
    )
    ids = set()
    for key, profile in profiles.items():
        _require(profile["model_key"] == key, f"{key}: identity mismatch")
        expected_upstream = registry[key].get("upstream", key)
        _require(profile["upstream_id"] == expected_upstream, f"{key}: upstream mismatch")
        _require(profile["kind"] == registry[key]["type"], f"{key}: kind mismatch")
        suffix = "" if profile["transport"] != "responses" else "/latest"
        expected_uri = "gpt://${YANDEX_PROJECT_ID}/" + expected_upstream + suffix
        _require(profile["model_uri_template"] == expected_uri, f"{key}: URI mismatch")
        _require(profile["profile_id"] not in ids, f"{key}: duplicate profile ID")
        ids.add(profile["profile_id"])
        _require(profile["context"]["overflow"] == "fail_without_truncation", key)
        _require(profile["context"]["input"] == "complete_applicable_context", key)
        _require(bool(profile["blockers"]), f"{key}: prepared artifact needs blockers")
        _require(profile["verification"]["provider_acceptance"] == "NOT_RUN", key)
        _require(profile["verification"]["effective_parameters"] == "UNKNOWN", key)
        _require(profile["output"]["selection"] == "maximum_supported", key)
        _require(
            profile["output"]["documented_hosted_ceiling_tokens"] is None,
            f"{key}: undocumented hosted output ceiling",
        )
        for section in ("lifecycle", "output", "reasoning", "tools"):
            for source in profile[section].get("evidence", []):
                _require(source in catalog["sources"], f"{key}: unknown evidence source")
        wire = profile["wire_parameters"]
        if profile["status"] == "blocked_retired":
            _require(not wire, f"{key}: retired model must not have a wire request")
            _require(profile["lifecycle"]["state"] == "retired", key)
            _require(profile["lifecycle"]["stop_and_recheck_on"] is not None, key)
            _require(profile["output"]["requested"] is None, key)
            continue
        _require(profile["status"] == "prepared_not_activated", key)
        if profile["transport"] == "realtime":
            session = wire["session"]
            _require(wire["type"] == "session.update", key)
            _require(session["max_output_tokens"] == "inf", key)
            _require(session["output_modalities"] in (["audio"], ["text"]), key)
            _require(session["yc_timeout_seconds"] == 3600, key)
            forbidden = {"truncation", "max_response_output_tokens", "model", "background"}
            _require(not (forbidden & set(session)), f"{key}: foreign API fields")
        elif profile["transport"] == "text_generation_async":
            _require(profile["reasoning"]["supported"] is False, key)
            _require(not profile["reasoning"]["wire_parameters"], key)
            _require(profile["tools"]["function_calling"] is False, key)
            _require(not ({"tools", "reasoning", "service_tier"} & set(wire)), key)
            original = read_json(Path(root) / profile["existing_profile_ref"])
            expected = original["request"]["body"]["completionOptions"]
            _require(wire["completionOptions"] == expected, f"{key}: async profile drift")
            _require(profile["output"]["requested"] == expected["maxTokens"], key)
        else:
            _require(profile["transport"] == "responses", key)
            _require(wire["truncation"] == "disabled", key)
            _require("max_output_tokens" not in wire, f"{key}: guessed output ceiling")
            if key in {"gpt-oss-120b", "gpt-oss-20b"}:
                _require(wire.get("reasoning") == {"effort": "high"}, key)
            else:
                _require("reasoning" not in wire, f"{key}: unsupported reasoning claim")
            if key == "qwen3.6-35b-a3b":
                _require(profile["reasoning"]["depth_control"] is False, key)
                _require("reasoning_enable_wire_unverified" in profile["blockers"], key)
    return profiles


def require_executable_profile(catalog, model_key, as_of=None):
    """A visibility guard for future consumers, not a wired runtime admission gate."""
    if model_key not in catalog["profiles"]:
        raise ProfileError(f"unknown model key: {model_key}; no fallback")
    profile = catalog["profiles"][model_key]
    boundary = profile["lifecycle"]["stop_and_recheck_on"]
    today = as_of or date.today()
    if boundary and today >= date.fromisoformat(boundary):
        raise ProfileError(f"{model_key}: lifecycle boundary reached; stop and recheck")
    if profile["status"] != "active" or profile["blockers"]:
        raise ProfileError(f"{model_key}: prepared only: {', '.join(profile['blockers'])}")
    return deepcopy(profile)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--model", help="inspect one exact registry key; never generate")
    args = parser.parse_args()
    catalog = read_json(args.root / "config/model_profiles/ultra-profiles.json")
    registry = read_registry(args.root / "core/config.py")
    profiles = validate_catalog(catalog, registry, args.root)
    if args.model:
        if args.model not in profiles:
            parser.error("unknown model key; no fallback")
        result = profiles[args.model]
    else:
        result = {
            "status": "OFFLINE_VALIDATED_NOT_ACTIVATED",
            "profiles": len(profiles),
            "retired": sum(p["status"] == "blocked_retired" for p in profiles.values()),
            "provider_calls": 0,
            "live_acceptance": "NOT_RUN",
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
