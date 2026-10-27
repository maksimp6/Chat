"""Alice AI ART through the synchronous OpenAI-compatible Yandex Images API."""

from core.wikifunctions import trim_string

import base64
import binascii
from typing import Any

import requests

from core.config import IMAGE_MODELS, upstream_model_id

GENERATE_URL = "https://ai.api.cloud.yandex.net/v1/images/generations"
MAX_PROMPT_CHARS = 500
# Room for the supported 1536×1024 PNG output, including RGBA and metadata.
MAX_IMAGE_BYTES = 16 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 180
IMAGE_SIZES = {"auto", "1x1", "1024x1024", "1536x1024", "1024x1536"}


class ImageGenerationError(RuntimeError):
    pass


def build_generation_payload(
    project_id: str, model_key: str, prompt: str, size: str | None = None
) -> dict[str, Any]:
    if not isinstance(project_id, str):
        raise ValueError("Yandex project_id is required")
    project_id = trim_string(project_id)
    if not project_id:
        raise ValueError("Yandex project_id is required")
    if not isinstance(model_key, str) or model_key not in IMAGE_MODELS:
        raise ValueError("not_an_image_model")
    if not isinstance(prompt, str) or not trim_string(prompt):
        raise ValueError("message is required")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError("prompt_too_long")
    if size is not None and (not isinstance(size, str) or size not in IMAGE_SIZES):
        raise ValueError("invalid_image_size")
    payload = {
        "model": "art://" + project_id + "/" + upstream_model_id(model_key),
        "prompt": prompt,
    }
    if size is not None:
        payload["size"] = size
    return payload


def generate_image(headers: dict[str, str], payload: dict[str, Any]) -> tuple[bytes, str]:
    # Generation is paid and non-idempotent. Never retry this POST automatically.
    response = requests.post(
        GENERATE_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
    )
    response.raise_for_status()
    try:
        result = response.json()
    except ValueError as exc:
        raise ImageGenerationError("provider returned invalid JSON") from exc
    if not isinstance(result, dict) or "error" in result:
        raise ImageGenerationError("provider returned an image generation error")
    images = result.get("data")
    image = images[0] if isinstance(images, list) and len(images) == 1 else None
    encoded = image.get("b64_json") if isinstance(image, dict) else None
    if not isinstance(encoded, str) or not encoded:
        raise ImageGenerationError("provider returned no image")
    if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ImageGenerationError("generated image is larger than the artifact limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ImageGenerationError("generated image is not valid base64") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageGenerationError("generated image is larger than the artifact limit")
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        mime_type = "image/png"
    elif raw.startswith(b"\xff\xd8\xff"):
        mime_type = "image/jpeg"
    else:
        raise ImageGenerationError("provider returned an unsupported image format")
    return raw, mime_type
