"""Recover narrated audio from the single-step runner's checksummed result logs."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path

MAX_LOG_BYTES = 24 * 1024 * 1024
PART = re.compile(r"(?:^|\s)LOGOS_RESULT_PART=([0-9]+)/([0-9]+):([A-Za-z0-9+/=]+)$")
SHA = re.compile(r"(?:^|\s)LOGOS_RESULT_SHA256=([0-9a-f]{64})$")


def recover(log: str, output: Path) -> dict:
    if len(log.encode("utf-8")) > MAX_LOG_BYTES:
        raise ValueError("Log exceeds recovery limit")
    parts: list[str] = []
    total = None
    expected_hash = None
    for line in log.splitlines():
        match = PART.search(line)
        if match:
            index, count = int(match[1]), int(match[2])
            if index == 1:
                # A later final result supersedes the earlier result as a whole.
                parts, total, expected_hash = [], count, None
            if count != total or not 1 <= count <= 1000 or index != len(parts) + 1:
                raise ValueError("Missing, duplicated or unordered result chunk")
            parts.append(match[3])
        match = SHA.search(line)
        if match:
            expected_hash = match[1]
    if not parts or len(parts) != total or expected_hash is None:
        raise ValueError("Incomplete final result log")
    payload = base64.b64decode("".join(parts), validate=True)
    if hashlib.sha256(payload).hexdigest() != expected_hash:
        raise ValueError("Result checksum mismatch")
    result = json.loads(payload)
    if not isinstance(result, dict) or not isinstance(result.get("narration"), dict):
        raise TypeError("Result has no narration")
    receipt = dict(result["narration"])
    text = receipt.get("text")
    if not isinstance(text, str) or len(text) > 2500:
        raise ValueError("Invalid narration text")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != receipt.get("text_sha256"):
        raise ValueError("Narration text checksum mismatch")
    audio_parts = receipt.get("parts")
    if not isinstance(audio_parts, list) or len(audio_parts) > 3:
        raise ValueError("Invalid narration parts")
    files = {}
    clean_parts = []
    for index, part in enumerate(audio_parts, 1):
        name = f"speech-{index:03d}.ogg"
        if not isinstance(part, dict) or part.get("file") != name:
            raise ValueError("Invalid narration filename/order")
        encoded = part.get("audio_base64")
        if not isinstance(encoded, str) or len(encoded) > 3 * 1024 * 1024:
            raise ValueError("Invalid narration audio encoding")
        data = base64.b64decode(encoded, validate=True)
        if not 0 < len(data) <= 2 * 1024 * 1024:
            raise ValueError("Invalid narration audio size")
        if hashlib.sha256(data).hexdigest() != part.get("sha256"):
            raise ValueError("Audio checksum mismatch")
        if not data.startswith(b"OggS") or b"OpusHead" not in data[:512]:
            raise ValueError("Narration part is not OggOpus")
        files[name] = data
        clean_parts.append({key: value for key, value in part.items() if key != "audio_base64"})
    if receipt.get("audio_status") == "ready" and (
        not files or receipt.get("status") != "audio_ready"
    ):
        raise ValueError("Contradictory narration status")
    receipt.update(parts=clean_parts, delivery="recovered_from_log_playback_not_verified")
    # Validate every part before writing anything. Never reuse an old audio directory.
    output.mkdir(parents=True, exist_ok=False)
    (output / "story.txt").write_text(text, encoding="utf-8")
    for name, data in files.items():
        (output / name).write_bytes(data)
    (output / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    # Bounded read before parsing; a malformed log cannot consume arbitrary memory.
    with args.log_file.open("rb") as stream:
        raw = stream.read(MAX_LOG_BYTES + 1)
    if len(raw) > MAX_LOG_BYTES:
        raise ValueError("Log exceeds recovery limit")
    receipt = recover(raw.decode("utf-8"), args.out)
    print(json.dumps({key: receipt.get(key) for key in ("status", "audio_status", "delivery")}))
    return int(receipt.get("status") == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
