"""Alice AI ART through the Yandex Images API: start an operation, then read it.

Generation is asynchronous. The start call returns an operation id, and the result is
read from the operation endpoint once it is done. The documented result is a base64
image in ``response.image``.
"""

import base64
from typing import Any

import requests

from core.config import upstream_model_id

GENERATE_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/imageGenerationAsync"
OPERATION_URL = "https://operation.api.cloud.yandex.net/operations/"
MIME_TYPE = "image/jpeg"
MAX_IMAGE_BYTES = 4 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 60
# A poll is answered from the provider within this bound; the chat route holds each poll for 25 seconds.
OPERATION_TIMEOUT_SECONDS = 20


class ImageGenerationError(RuntimeError):
    pass


def build_generation_payload(project_id: str, model_key: str, prompt: str) -> dict[str, Any]:
    return {
        "modelUri": "art://" + project_id.strip() + "/" + upstream_model_id(model_key),
        "messages": [{"text": prompt}],
        "generationOptions": {"mimeType": MIME_TYPE},
    }


def start_generation(headers: dict[str, str], payload: dict[str, Any]) -> str:
    response = requests.post(
        GENERATE_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
    )
    response.raise_for_status()
    operation_id = response.json().get("id")
    if not isinstance(operation_id, str) or not operation_id:
        raise ImageGenerationError("provider returned no operation id")
    return operation_id


def read_operation(headers: dict[str, str], operation_id: str) -> dict[str, Any]:
    response = requests.get(
        OPERATION_URL + operation_id, headers=headers, timeout=OPERATION_TIMEOUT_SECONDS
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ImageGenerationError("provider returned an invalid operation")
    return data


def image_from_operation(operation: dict[str, Any]) -> str | None:
    """Return a data URL once the operation has produced an image, or None while it runs."""
    error = operation.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else None
        raise ImageGenerationError(str(message or "generation failed")[:200])
    if not operation.get("done"):
        return None
    response = operation.get("response")
    encoded = response.get("image") if isinstance(response, dict) else None
    if not isinstance(encoded, str) or not encoded:
        raise ImageGenerationError("operation finished without an image")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ImageGenerationError("generated image is not valid base64") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageGenerationError("generated image is larger than the chat limit")
    return "data:" + MIME_TYPE + ";base64," + encoded
