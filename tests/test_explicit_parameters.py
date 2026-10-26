"""Every request parameter is explicit, the way an Ultra profile names each setting.

A parameter is either sent with its value, or recorded with the reason it is left to the provider.
"""

from yandex_client_modules.request_builder import (
    build_chat_image_payload,
    build_response_payload,
    explicit_parameters,
    ultra_applied_event,
)

QWEN = "qwen3.6-35b-a3b"
RESPONSES_PARAMETERS = {
    "background",
    "store",
    "stream",
    "temperature",
    "top_p",
    "max_output_tokens",
    "truncation",
    "reasoning",
    "service_tier",
    "tool_choice",
    "max_tool_calls",
    "parallel_tool_calls",
}


def test_responses_request_names_every_parameter_with_a_value_or_a_reason():
    payload = build_response_payload(
        "folder", QWEN, "hi", params={"temperature": 0.7, "top_p": 1.0, "max_output_tokens": 0}
    )

    parameters = explicit_parameters(QWEN, payload)

    assert set(parameters) == RESPONSES_PARAMETERS
    assert all(entry["sent"] or entry["reason"] for entry in parameters.values())
    assert parameters["stream"] == {"sent": True, "value": False}
    assert parameters["truncation"] == {"sent": True, "value": "disabled"}
    assert parameters["max_output_tokens"]["sent"] is False
    assert parameters["max_output_tokens"]["reason"] == "provider_ceiling_unpublished_do_not_use_context_as_output"
    assert parameters["reasoning"]["sent"] is False
    assert parameters["reasoning"]["reason"] == "family_support_no_depth_exact_enable_wire_unverified"
    assert parameters["tool_choice"]["reason"] == "no_tools_in_request"
    assert parameters["service_tier"]["reason"] == "provider_default"


def test_photo_request_sends_stream_and_top_p_and_names_the_rest():
    payload = build_chat_image_payload(
        "folder", QWEN, "what is on the photo", ["data:image/png;base64,AAAA"], temperature=0.7, top_p=0.9
    )

    assert payload["stream"] is False
    assert payload["top_p"] == 0.9

    parameters = explicit_parameters(QWEN, payload)

    assert set(parameters) == {"stream", "temperature", "top_p", "max_tokens", "tools"}
    assert parameters["temperature"] == {"sent": True, "value": 0.7}
    assert parameters["max_tokens"]["reason"] == "provider_ceiling_unpublished_do_not_use_context_as_output"
    assert parameters["tools"]["reason"] == "photo_request_has_no_tool_loop"


def test_profile_event_in_the_trace_carries_the_explicit_parameters():
    payload = build_response_payload("folder", QWEN, "hi", params={})

    event = ultra_applied_event(QWEN, {}, payload)

    assert event["parameters"] == explicit_parameters(QWEN, payload)
    assert event["parameters"]["temperature"] == {"sent": False, "value": None, "reason": "not_set_by_request"}
