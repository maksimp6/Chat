"""Photo input: validation, the Chat Completions payload, the reply shape and the photo flag of the catalog.

The URI of the photo model is not asserted here: it is not confirmed against the provider docs yet.
"""

import base64

import pytest

from chat.mcp_routes import IMAGE_INPUT_MODEL_KEYS, MAX_IMAGE_BYTES, MAX_IMAGES_PER_MESSAGE, _validated_images
from core.config import TEXT_MODELS
from provider_accounts.model_discovery import ModelDiscovery
from yandex_client_modules.request_builder import build_chat_image_payload, chat_completion_to_response
from yandex_client_modules.errors import YandexClientError

PNG_HEADER = b"\x89PNG\r\n\x1a\n"
JPEG_HEADER = b"\xff\xd8\xff"


def _data_url(mime, payload):
    return "data:" + mime + ";base64," + base64.b64encode(payload).decode("ascii")


def test_accepts_png_and_jpeg_data_urls():
    png = _data_url("image/png", PNG_HEADER + b"0" * 16)
    jpeg = _data_url("image/jpeg", JPEG_HEADER + b"0" * 16)

    assert _validated_images([{"data_url": png}, {"data_url": jpeg}]) == [png, jpeg]


def test_no_attachments_means_no_images():
    assert _validated_images(None) == []


def test_rejects_content_that_does_not_match_its_declared_type():
    declared_jpeg_but_png = _data_url("image/jpeg", PNG_HEADER + b"0" * 16)

    with pytest.raises(ValueError, match="does not match"):
        _validated_images([{"data_url": declared_jpeg_but_png}])


def test_rejects_more_than_the_photo_limit():
    urls = [{"data_url": _data_url("image/png", PNG_HEADER)}] * (MAX_IMAGES_PER_MESSAGE + 1)

    with pytest.raises(ValueError, match="at most"):
        _validated_images(urls)


def test_rejects_urls_that_are_not_base64_data_urls():
    with pytest.raises(ValueError, match="base64 data URL"):
        _validated_images([{"data_url": "https://example.com/photo.png"}])
    with pytest.raises(ValueError, match="base64 data URL"):
        _validated_images([{"data_url": "data:image/png,not-base64"}])


def test_rejects_an_image_over_the_size_limit():
    too_big = PNG_HEADER + b"0" * (MAX_IMAGE_BYTES + 1 - len(PNG_HEADER))

    with pytest.raises(ValueError, match="5 MB"):
        _validated_images([{"data_url": _data_url("image/png", too_big)}])


def test_only_registry_models_marked_multimodal_accept_photos():
    assert IMAGE_INPUT_MODEL_KEYS
    assert all((TEXT_MODELS[key].get("capabilities") or {}).get("multimodal") is True for key in IMAGE_INPUT_MODEL_KEYS)
    assert "yandexgpt-5-lite" not in IMAGE_INPUT_MODEL_KEYS


def test_chat_payload_puts_system_then_history_then_text_and_images():
    model_key = sorted(IMAGE_INPUT_MODEL_KEYS)[0]
    images = [_data_url("image/png", PNG_HEADER)]
    history = [{"role": "user", "text": "earlier question"}, {"role": "assistant", "text": "earlier answer"}]

    payload = build_chat_image_payload(
        project_id="folder",
        model_key=model_key,
        message="what is on the photo",
        images=images,
        instructions="be brief",
        history=history,
    )

    assert payload["model"].startswith("gpt://folder/")
    messages = payload["messages"]
    assert messages[0] == {"role": "system", "content": "be brief"}
    assert messages[1:3] == [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]
    assert messages[3]["role"] == "user"
    assert messages[3]["content"] == [
        {"type": "text", "text": "what is on the photo"},
        {"type": "image_url", "image_url": {"url": images[0]}},
    ]


def test_chat_completion_reply_is_reshaped_to_the_responses_fields():
    data = {
        "id": "resp-1",
        "choices": [{"finish_reason": "stop", "message": {"content": "A cat."}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
    }

    shaped = chat_completion_to_response(data)

    assert shaped["status"] == "completed"
    assert shaped["output"][0]["content"][0]["text"] == "A cat."
    assert shaped["usage"] == {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}


def test_truncated_reply_is_incomplete_and_filtered_reply_is_an_error():
    truncated = {"choices": [{"finish_reason": "length", "message": {"content": "A cat"}}]}
    assert chat_completion_to_response(truncated)["status"] == "incomplete"

    filtered = {"choices": [{"finish_reason": "content_filter", "message": {"content": "x"}}]}
    with pytest.raises(YandexClientError):
        chat_completion_to_response(filtered)


def test_catalog_photo_flag_comes_from_the_registry_not_from_the_provider():
    provider_claims_photos = {"id": "some-other-model", "capabilities": {"multimodal": True}}
    registry_model = {"id": "qwen3.6-35b-a3b", "capabilities": {"multimodal": False}}

    assert ModelDiscovery._normalize_model(provider_claims_photos)["capabilities"]["multimodal"] is None
    assert ModelDiscovery._normalize_model(registry_model)["capabilities"]["multimodal"] is True
