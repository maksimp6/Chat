"""Validate the prepared Yandex-wide catalog offline; never activate or call a model."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse


class CatalogError(ValueError):
    """Catalog coverage or a prepared model contract is inconsistent."""


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CatalogError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique)


def require(condition, message):
    if not condition:
        raise CatalogError(message)


def _check_endpoints(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"endpoint", "endpoint_template"} and item:
                host = urlparse(item if "://" in item else "grpcs://" + item).hostname
                require(
                    host
                    in {
                        "ai.api.cloud.yandex.net",
                        "tts.api.cloud.yandex.net",
                        "stt.api.cloud.yandex.net",
                        "transcribe.api.cloud.yandex.net",
                        "operation.api.cloud.yandex.net",
                    },
                    f"non-Yandex endpoint: {host}",
                )
            _check_endpoints(item)
    elif isinstance(value, list):
        for item in value:
            _check_endpoints(item)


def validate_catalog(catalog, legacy):
    require(catalog["schema_version"] == 2, "schema version")
    require(catalog["provider"] == "yandex", "Yandex provider required")
    policy = catalog["policy"]
    require(policy["activation"] == "NOT_ACTIVATED", "catalog activation forbidden")
    require(policy["automatic_generation"] is False, "automatic generation forbidden")
    require(
        policy["runtime_adapter_included"] is False, "runtime integration not proven"
    )
    require(policy["model_fallback"] is None, "model fallback forbidden")
    require(policy["silent_parameter_downgrade"] is None, "silent downgrade forbidden")
    require(
        type(policy["automatic_post_retries"]) is int, "retry count must be integer"
    )
    require(policy["automatic_post_retries"] == 0, "paid POST retries forbidden")
    require(
        policy["requires_explicit_run_scope_and_budget"] is True, "run approval missing"
    )
    profiles = catalog["profiles"]
    inventory = catalog["coverage"]["expected_profile_ids_by_surface"]
    expected = [key for keys in inventory.values() for key in keys]
    require(len(expected) == len(set(expected)), "duplicate source inventory row")
    require(set(expected) == set(profiles), "source inventory coverage mismatch")
    require(
        catalog["coverage"]["profile_count_including_templates"] == len(profiles),
        "profile count mismatch",
    )
    counts = dict(Counter(p["surface"] for p in profiles.values()))
    require(
        counts == catalog["coverage"]["counts_by_surface"], "surface count mismatch"
    )
    for surface, keys in inventory.items():
        require(
            all(profiles[key]["surface"] == surface for key in keys), "wrong surface"
        )

    conflicts = {entry["id"]: entry for entry in catalog["source_conflicts"]}
    required_conflicts = {
        "embedding_input_ceiling_source_conflict",
        "embedding_v2_dimensions_general_limits_stale",
        "fine_tuned_embedding_uri_scheme_conflict",
    }
    require(required_conflicts <= set(conflicts), "source conflict record missing")
    input_claims = conflicts["embedding_input_ceiling_source_conflict"]["claims"]
    require(
        {(p["source"], p["value"]) for p in input_claims}
        == {
            ("embedding_openai", 8192),
            ("limits", 2048),
        },
        "embedding input source claims changed",
    )
    dimension_claims = conflicts["embedding_v2_dimensions_general_limits_stale"][
        "claims"
    ]
    require(
        any(
            p["source"] == "embedding_catalog" and p["value"] == [128, 256, 512, 768]
            for p in dimension_claims
        ),
        "v2 dimension source claim missing",
    )
    require(
        any(p["source"] == "limits" and p["value"] == 256 for p in dimension_claims),
        "generic dimension conflict lost",
    )
    uri_claims = conflicts["fine_tuned_embedding_uri_scheme_conflict"]["claims"]
    require(
        {(p["source"], p["scheme"]) for p in uri_claims}
        == {
            ("embedding_catalog", "gpt"),
            ("tuned_embedding", "emb"),
            ("model_list", "emb"),
        },
        "tuned embedding URI source claims changed",
    )

    # Preserve registry identities and original accepted candidates while expanding coverage.
    require(
        set(catalog["registry_snapshot"]["legacy_model_keys"])
        == set(legacy["profiles"]),
        "legacy registry coverage mismatch",
    )
    for key, source in legacy["profiles"].items():
        current = profiles["base:" + key]
        require(current["plan"] == source, f"legacy profile drift: {key}")
        require(current["model_id"] == source["upstream_id"], "legacy upstream drift")
        require(
            current["model_uri_template"] == source["model_uri_template"], "URI drift"
        )

    allowed_states = {
        "prepared_not_activated",
        "blocked_source_conflict",
        "blocked_service_disabled",
        "blocked_retired",
        "account_specific_template",
        "blocked_contract_unverified",
    }
    for key, profile in profiles.items():
        require(key == profile["profile_id"], "profile identity mismatch")
        require(profile["provider"] == "yandex", "foreign provider")
        require(profile["status"] in allowed_states, "executable status forbidden")
        require(
            profile["activation"] == "NOT_ACTIVATED", "profile activation forbidden"
        )
        require(
            profile["provider_acceptance"] == "NOT_RUN", "unproven provider acceptance"
        )
        require(
            profile["account_availability"] == "NOT_CHECKED", "unproven account access"
        )
        require(bool(profile["blockers"]), "prepared profile needs blockers")
        require(bool(profile["source_urls"]), "missing source evidence")
        namespace = catalog["sources"][profile["source_namespace"]]
        known_urls = {source["url"] for source in namespace.values()}
        require(set(profile["source_urls"]) <= known_urls, "unknown source URL")
        plan = profile["plan"]
        _check_endpoints(plan)
        require(
            set(plan.get("blockers", [])) <= set(profile["blockers"]),
            "plan blockers lost",
        )
        if "status" in plan:
            require(
                plan["status"] == profile["status"], "envelope/plan status mismatch"
            )
        if "model_id" in plan:
            require(
                plan["model_id"] == profile["model_id"], "envelope/plan model mismatch"
            )
        if "model_uri_template" in plan:
            require(
                plan["model_uri_template"] == profile["model_uri_template"],
                "envelope/plan URI mismatch",
            )
        if "uri_template" in plan:
            require(
                plan["uri_template"] == profile["model_uri_template"],
                "envelope/plan URI mismatch",
            )
        surface = profile["surface"]

        if surface == "retired_base":
            require(profile["status"] == "blocked_retired", "retired model unblocked")
            require(not plan["wire_parameters"], "retired model has a request")
        elif surface == "dedicated" or surface.startswith("batch_"):
            require(
                profile["status"] == "blocked_service_disabled",
                "disabled service unblocked",
            )
            require(not plan["wire_parameters"], "disabled service has a request")
            require(
                plan["documented_hosted_output_ceiling_tokens"] is None,
                "guessed output",
            )
            if surface == "dedicated":
                require(profile["model_id"] is None, "invented dedicated model ID")
                require(profile["model_uri_template"] is None, "invented dedicated URI")
                require(
                    all(
                        plan[field] is None
                        for field in ("model_id", "model_version", "model_uri")
                    ),
                    "invented dedicated plan identifiers",
                )
            else:
                require(
                    profile["model_uri_template"] == plan["model_uri_template"],
                    "batch URI",
                )
                require(
                    plan["transport"] == "batch_dataset", "batch is not completionAsync"
                )
        elif surface == "embedding":
            require(plan["input_max_tokens"] is None, "unresolved input limit conflict")
            require(
                "embedding_input_ceiling_source_conflict" in plan["blockers"],
                "conflict lost",
            )
            body = plan["request_template"]
            require(
                not (
                    {"reasoning", "temperature", "max_output_tokens", "tools"}
                    & set(body)
                ),
                "embedding foreign fields",
            )
            require(
                body["model"] == profile["model_uri_template"], "embedding URI drift"
            )
            if "-v2-" in plan["key"]:
                require(body["dimensions"] == 768, "embedding maximum dimension drift")
                rest = plan["alternative_rest"]
                require(
                    rest["endpoint"]
                    == "https://ai.api.cloud.yandex.net/foundationModels/v1/textEmbedding",
                    "embedding REST endpoint drift",
                )
                require(
                    rest["request_template"]["dim"] == "768", "REST dimension drift"
                )
                require(
                    rest["request_template"]["modelUri"]
                    == profile["model_uri_template"],
                    "REST embedding URI drift",
                )
                require(
                    set(rest["request_template"]) == {"modelUri", "text", "dim"},
                    "REST embedding fields",
                )
            else:
                require("dimensions" not in body, "v1 dimension selector unsupported")
        elif surface == "classifier":
            require(
                plan["labels"]
                == {
                    "minimum": 2,
                    "maximum": 20,
                    "selection": "exact_task_labels_with_meaningful_names",
                },
                "classifier label contract",
            )
            require(
                plan["multilabel"] is False, "prompt classifier multilabel unsupported"
            )
            require(plan["synchronous_only"] is True, "classifier transport drift")
            require(
                plan["samples"]["maximum_total_tokens"] == 6000,
                "classifier sample limit",
            )
            require(
                set(plan["request_fields"])
                == {
                    "modelUri",
                    "taskDescription",
                    "labels",
                    "text",
                    "samples",
                },
                "classifier request fields",
            )
            require(
                plan["endpoint"]
                == "https://ai.api.cloud.yandex.net/foundationModels/v1/fewShotTextClassification",
                "classifier endpoint drift",
            )
        elif surface == "base_image":
            require(plan["input"]["max_prompt_characters"] == 500, "image prompt limit")
            for body in plan["prepared_request_templates"].values():
                require(
                    set(body) == {"model", "prompt", "size"}, "image unsupported fields"
                )
                require(
                    body["model"] == profile["model_uri_template"], "image URI drift"
                )
                require(
                    body["size"] in {"1024x1024", "1536x1024", "1024x1536"},
                    "image size",
                )
        elif surface == "base_realtime" and "prepared_event_template" in plan:
            session = plan["prepared_event_template"]["session"]
            require(session["yc_timeout_seconds"] == 3600, "realtime session boundary")
            require(
                session["output_modalities"] == ["audio"], "realtime output modality"
            )
            require(
                "speed" not in session["audio"]["output"], "LiveTTS speed unsupported"
            )
            if plan["kind"] == "realtime_tts_only":
                require(
                    not ({"tools", "instructions", "max_output_tokens"} & set(session)),
                    "TTS-only cannot use LLM controls",
                )
                require(
                    "input" not in session["audio"],
                    "TTS-only cannot accept audio input",
                )
            else:
                require(session["max_output_tokens"] == 393216, "max TTS output drift")
        elif surface == "account_template":
            require(
                profile["model_id"] is None, "private template is not a deployed model"
            )
            require(
                profile["status"] == "account_specific_template",
                "private template status",
            )
            require(
                plan["not_counted_as_deployed_model"] is True, "private model count"
            )
            if plan["kind"] == "classifier":
                require(
                    plan["input_max_tokens"] == 8000, "tuned classifier input limit"
                )
                require(
                    plan["trained_classes_max"] == 100, "tuned classifier class limit"
                )
                require(
                    set(plan["request_fields"]) == {"modelUri", "text"},
                    "tuned classifier request fields",
                )
            elif plan["kind"] == "embedding":
                require(
                    plan["dimensions"]["selected"] is None, "invented tuned dimensions"
                )
                require(
                    plan["uri_template"].startswith("emb://"),
                    "tuned embedding URI scheme",
                )
                require(
                    "fine_tuned_embedding_uri_scheme_conflict" in plan["blockers"],
                    "tuned embedding conflict lost",
                )
        elif surface in {"speechkit_stt", "speechkit_tts"}:
            require(profile["model_uri_template"] is None, "SpeechKit is not gpt URI")
            require(
                plan["model_selector"] == profile["model_id"],
                "SpeechKit selector drift",
            )
            primary = plan["primary_transport"]
            body = primary["request_body_template"]
            require(plan["max_output_tokens"] is None, "SpeechKit is not LLM tokens")
            if surface == "speechkit_stt":
                require(
                    body["recognitionModel"]["model"] == profile["model_id"],
                    "STT selector drift",
                )
                require(
                    primary["endpoint"]
                    == "https://stt.api.cloud.yandex.net/stt/v3/recognizeFileAsync",
                    "STT endpoint drift",
                )
                require(
                    not ("content" in body and "uri" in body), "STT conflicting inputs"
                )
                if profile["model_id"].startswith("deferred-general"):
                    require(
                        plan["streaming_supported"] is False,
                        "deferred STT is async only",
                    )
            else:
                require(body["model"] == profile["model_id"], "TTS selector drift")
                require(
                    primary["endpoint"]
                    == "https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis",
                    "TTS endpoint drift",
                )
                require(
                    body["unsafeMode"] is False, "unsafe mode is not maximum quality"
                )
                require(
                    all(len(hint) == 1 for hint in body["hints"]), "TTS oneof hints"
                )
                if profile["model_id"] == "livetts":
                    forbidden = {"audioTemplate", "speed", "pitchShift", "duration"}
                    require(
                        all(not (set(hint) & forbidden) for hint in body["hints"]),
                        "LiveTTS unsupported hint",
                    )

    # Same named model has distinct provider limits in different deployment surfaces.
    require(
        profiles["yandex-dedicated:qwen2.5-vl-32b-instruct"]["plan"][
            "context_window_tokens"
        ]
        == 32768,
        "dedicated context drift",
    )
    require(
        profiles["yandex-batch:multimodal:qwen2.5-vl-32b-instruct"]["plan"][
            "context_window_tokens"
        ]
        == 4096,
        "batch context drift",
    )
    require(
        catalog["deployment_notes"]["common_async_is_not_batch"] is True,
        "transport mixing",
    )
    return {
        "status": "OFFLINE_VALIDATED_NOT_ACTIVATED",
        "profiles_including_templates": len(profiles),
        "counts_by_surface": counts,
        "provider_acceptance": "NOT_RUN",
        "account_specific_inventory": "NOT_CHECKED",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    base = args.root / "config/model_profiles"
    result = validate_catalog(
        read_json(base / "yandex-ultra-profiles.json"),
        read_json(base / "ultra-profiles.json"),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
