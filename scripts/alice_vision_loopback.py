"""Isolated loopback-only CPU vision endpoint and reproducible private E2E.

The endpoint never receives or loads expected labels, case IDs, files or URLs.
Only the offline bootstrap tool sees a trusted local training gallery.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from scripts.alice_vision_shapes import (
    SCHEMA,
    load_model,
    model_digest,
    predict,
    save_model,
    train,
    validate_model,
)
from scripts.visual_object_benchmark import run_benchmark
from scripts.visual_object_contract import load_cases

MAX_WIRE = 11 * 1024 * 1024
MAX_PNG = 8 * 1024 * 1024
MAX_PROMPT_CHARS = 2048
_ALLOWED_KEYS = frozenset({"image_base64", "image_sha256", "mime_type", "prompt", "case_id"})


class LocalVisionHTTP(HTTPServer):
    allow_reuse_address = False

    def __init__(self, model: dict, port: int = 0):
        validate_model(model)
        # No configurable hostname; remote network bind is impossible.
        super().__init__(("127.0.0.1", port), LocalVisionHandler)
        self.vision_model = model
        self.vision_sha256 = model_digest(model)


class LocalVisionHandler(BaseHTTPRequestHandler):
    server: LocalVisionHTTP
    protocol_version = "HTTP/1.0"

    def log_message(self, *args) -> None:
        """Never print prompts, photos, body contents or client identifiers."""

    def _json(self, status: int, value: dict) -> None:
        blob = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(blob)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _trusted_local_request(self) -> bool:
        port = self.server.server_port
        return self.client_address[0] == "127.0.0.1" and self.headers.get("Host") in {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
        }

    def do_GET(self) -> None:
        if not self._trusted_local_request():
            self._json(403, {"error": "forbidden_origin"})
        elif self.path == "/healthz":
            self._json(
                200,
                {
                    "status": "ready",
                    "backend": SCHEMA,
                    "model_sha256": self.server.vision_sha256,
                    "scope": "cad-render-only",
                    "bind": "127.0.0.1",
                },
            )
        else:
            self._json(404, {"error": "not_found"})

    def _decode(self, raw: bytes) -> bytes:
        request = json.loads(raw)
        if not isinstance(request, dict) or set(request) - _ALLOWED_KEYS:
            raise ValueError("unrecognized request fields")
        if request.get("mime_type") != "image/png":
            raise ValueError("only PNG inputs are accepted")
        if not isinstance(request.get("prompt"), str):
            raise TypeError("prompt must be a string")
        if len(request["prompt"]) > MAX_PROMPT_CHARS:
            raise ValueError("prompt exceeds limit")
        image_b64 = request.get("image_base64")
        if not isinstance(image_b64, str) or len(image_b64) > MAX_WIRE:
            raise ValueError("image base64 payload missing/oversized")
        try:
            blob = base64.b64decode(image_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image base64 is invalid") from exc
        if not 24 <= len(blob) <= MAX_PNG or blob[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("input must be a bounded PNG")
        digest = request.get("image_sha256")
        if not isinstance(digest, str) or hashlib.sha256(blob).hexdigest() != digest:
            raise ValueError("image SHA-256 mismatch")
        return blob

    def do_POST(self) -> None:
        if not self._trusted_local_request():
            return self._json(403, {"error": "forbidden_origin"})
        if self.path != "/recognize":
            return self._json(404, {"error": "not_found"})
        if self.headers.get("Content-Type") != "application/json":
            return self._json(415, {"error": "unsupported_media_type"})
        try:
            amount = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._json(411, {"error": "content_length_required"})
        if amount < 2 or amount > MAX_WIRE:
            return self._json(413, {"error": "invalid_body_size"})
        self.connection.settimeout(10)
        try:
            raw = self.rfile.read(amount)
        except TimeoutError:
            return self._json(408, {"error": "request_timed_out"})
        if len(raw) != amount:
            return self._json(400, {"error": "truncated_body"})
        try:
            image = self._decode(raw)
            # Note: prompt and case_id are intentionally NEVER passed to predict().
            response = predict(image, self.server.vision_model)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, OSError):
            return self._json(400, {"error": "invalid_image_or_request"})
        self._json(200, response)


def local_e2e(
    training: Path,
    evaluation: Path,
    gallery: Path,
    results: Path,
    model_path: Path,
    *,
    evaluation_gallery: Path | None = None,
) -> dict:
    """Actual local pixels -> HTTP -> classification -> IoU; never fake responses."""
    expected_model = train(training, gallery)
    expected_digest = model_digest(expected_model)
    if model_path.exists():
        model = load_model(model_path, expected_sha256=expected_digest)
    else:
        model = expected_model
        save_model(model, model_path)
    train_cases = load_cases(training)
    eval_cases = load_cases(evaluation)
    seen = {x["image_sha256"] for x in train_cases}
    overlap = sum(x["image_sha256"] in seen for x in eval_cases)
    server = LocalVisionHTTP(model)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        report = run_benchmark(
            evaluation,
            evaluation_gallery if evaluation_gallery is not None else gallery,
            results,
            endpoint=f"http://127.0.0.1:{server.server_port}/recognize",
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    if overlap and report["status"] == "EVALUATED":
        report["status"] = "REVIEW_PENDING"  # in-sample inference is never accepted
    report["engine"] = {
        "name": SCHEMA,
        "model_sha256": model_digest(model),
        "trained_on": model["training_kind"],
        "training_case_count": len(train_cases),
        "evaluation_case_count": len(eval_cases),
        "shared_source_images": overlap,
        "evaluation_leakage_warning": overlap > 0,
        "augmented_test_cases": sum("augmentation" in case for case in eval_cases),
        "augmentation_modes": sorted(
            {case["augmentation"] for case in eval_cases if "augmentation" in case}
        ),
    }
    report["conclusion"] = (
        "IN_SAMPLE_ONLY"
        if overlap
        else "SYNTHETIC_PERTURBATIONS_ONLY"
        if any("augmentation" in case for case in eval_cases)
        else "HELDOUT_RENDER_ONLY"
        if all(x.get("source_kind") == "cad_render" for x in eval_cases)
        else "PHOTO_TEST_REQUIRES_INDEPENDENT_REVIEW"
    )
    (results / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="command", required=True)
    test = sub.add_parser("evaluate", help="Train locally and run model through HTTP E2E")
    test.add_argument("--train-manifest", type=Path, required=True)
    test.add_argument("--eval-manifest", type=Path, required=True)
    test.add_argument("--gallery", type=Path, required=True)
    test.add_argument("--eval-gallery", type=Path)
    test.add_argument("--results", type=Path, required=True)
    test.add_argument("--model", type=Path, required=True)
    serve = sub.add_parser("serve", help="Serve a pinned private model on loopback only")
    serve.add_argument("--model", type=Path, required=True)
    serve.add_argument("--model-sha256", required=True)
    serve.add_argument("--port", type=int, default=8765)
    args = cli.parse_args()
    try:
        if args.command == "serve":
            model = load_model(args.model, expected_sha256=args.model_sha256)
            server = LocalVisionHTTP(model, args.port)
            print("LOCAL_VISION_READY", server.server_port, server.vision_sha256, flush=True)
            try:
                server.serve_forever(poll_interval=0.1)
            finally:
                server.server_close()
            return 0
        report = local_e2e(
            args.train_manifest,
            args.eval_manifest,
            args.gallery,
            args.results,
            args.model,
            evaluation_gallery=args.eval_gallery,
        )
        print(
            "LOCAL_VISION_E2E",
            report["status"],
            report["conclusion"],
            json.dumps(report["total"], sort_keys=True),
            flush=True,
        )
        return {
            "EVALUATED": 0,
            "REVIEW_PENDING": 2,
            "BLOCKED": 3,
            "FAILED": 4,
        }[report["status"]]
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print("LOCAL_VISION_BLOCKED", type(exc).__name__, str(exc), file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
