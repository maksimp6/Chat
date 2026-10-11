"""Fixture integrity, real-image crop path, and opt-in loopback HTTP E2E tests."""

from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import threading
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scripts.visual_object_benchmark import (
    _request_prediction,
    _safe_image,
    _validate_endpoint,
    run_benchmark,
)

try:
    import PIL  # noqa: F401

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


def tiny_png() -> bytes:
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    width, height = 16, 16
    pixels = b"".join(b"\0" + bytes((40, 100, 150)) * width for _ in range(height))
    return (
        bytes.fromhex("89504e470d0a1a0a")
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def image_case(data):
    return {
        "id": "sample",
        "image": "sample.png",
        "image_sha256": hashlib.sha256(data).hexdigest(),
        "crop": [0, 0, 16, 16],
        "annotation_status": "provisional",
        "objects": [{"label": "spool_holder", "bbox": [0.1, 0.1, 0.9, 0.9]}],
        "forbidden_labels": ["cutting_spindle"],
        "forbidden_claims": ["motor_500w"],
    }


def prediction(*, label="spool_holder"):
    return {
        "objects": [{"label": label, "bbox": [0.1, 0.1, 0.9, 0.9], "confidence": 0.98}],
        "claims": [],
    }


class VisualBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image_root = self.root / "images"
        self.image_root.mkdir()
        self.data = tiny_png()
        (self.image_root / "sample.png").write_bytes(self.data)
        self.case = image_case(self.data)
        self.manifest = self.root / "cases.json"
        self.manifest.write_text(json.dumps([self.case]))

    def test_integrity_pinned_image(self):
        path, blob = _safe_image(self.image_root, self.case)
        self.assertEqual(path.name, "sample.png")
        self.assertEqual(blob, self.data)

    def test_tampered_image_fails(self):
        (self.image_root / "sample.png").write_bytes(self.data + b" ")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            _safe_image(self.image_root, self.case)

    def test_symlink_escape_is_rejected(self):
        outside = self.root / "outside.png"
        outside.write_bytes(self.data)
        (self.image_root / "sample.png").unlink()
        (self.image_root / "sample.png").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            _safe_image(self.image_root, self.case)

    def test_traversal_and_unsupported_suffix_rejected(self):
        for name in ("../outside.png", "http://invalid/image.png", "sample.svg"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                _safe_image(self.image_root, {**self.case, "image": name})

    def test_explicit_loopback_only(self):
        _validate_endpoint("http://127.0.0.1:8080/recognize")
        for url in (
            "https://127.0.0.1:8080/recognize",
            "http://example.org:8080/recognize",
            "http://127.0.0.1:8080@evil.com/run",
            "file:///etc/passwd",
            "http://127.0.0.1/path",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _validate_endpoint(url)

    def test_http_sends_bytes_to_local_stub_without_model_claim(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                received.append(json.loads(raw))
                response = json.dumps(prediction()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            address = f"http://127.0.0.1:{server.server_port}/recognize"
            response = _request_prediction(address, self.case, self.data)
            alternative = {
                **self.case,
                "id": "different_ground_truth",
                "objects": [{"label": "phone_stand", "bbox": [0.1, 0.1, 0.9, 0.9]}],
                "forbidden_labels": ["cnc_mill"],
            }
            _request_prediction(address, alternative, self.data)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(response, prediction())
        self.assertEqual(received[0]["image_sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(received[0]["case_id"], hashlib.sha256(self.data).hexdigest()[:16])
        self.assertNotEqual(received[0]["case_id"], self.case["id"])
        self.assertEqual(received[0]["prompt"], received[1]["prompt"])
        self.assertIn("geometry", received[0]["prompt"])
        self.assertNotIn("authorization", received[0])

    @unittest.skipUnless(HAS_PILLOW, "Pillow is optional for image E2E")
    def test_offline_response_generates_annotated_preview_not_model_pass(self):
        folder = self.root / "results"
        folder.mkdir()
        (folder / "sample.json").write_text(json.dumps(prediction()))
        report = run_benchmark(
            self.manifest, self.image_root, self.root / "output", predictions_dir=folder
        )
        self.assertEqual(report["status"], "REVIEW_PENDING")
        self.assertEqual(report["total"]["tp"], 1)
        self.assertEqual(report["backend"], "offline_responses")
        self.assertTrue((self.root / "output/overlays/sample.png").is_file())

    @unittest.skipUnless(HAS_PILLOW, "Pillow is optional for image E2E")
    def test_missing_model_inference_is_blocked_not_passed(self):
        report = run_benchmark(self.manifest, self.image_root, self.root / "no_inference")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["evaluated"], 0)
        self.assertIsNone(report["total"]["precision"])
        self.assertIsNone(report["total"]["recall"])

    @unittest.skipUnless(HAS_PILLOW, "Pillow is optional for image E2E")
    def test_hallucinated_cutter_causes_failed_e2e(self):
        folder = self.root / "predictions"
        folder.mkdir()
        (folder / "sample.json").write_text(json.dumps(prediction(label="cutting_spindle")))
        report = run_benchmark(
            self.manifest, self.image_root, self.root / "bad", predictions_dir=folder
        )
        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(report["total"]["fn"], 1)
        self.assertEqual(report["cases"][0]["metrics"]["forbidden_found"], ["cutting_spindle"])

    @unittest.skipUnless(HAS_PILLOW, "Pillow is optional for image E2E")
    def test_valid_blank_crop_abstention(self):
        self.case["objects"] = []
        self.manifest.write_text(json.dumps([self.case]))
        folder = self.root / "predictions"
        folder.mkdir()
        (folder / "sample.json").write_text(json.dumps({"objects": [], "claims": []}))
        report = run_benchmark(
            self.manifest, self.image_root, self.root / "blank", predictions_dir=folder
        )
        self.assertEqual(report["status"], "REVIEW_PENDING")
        self.assertEqual(report["total"]["precision"], 1.0)
        self.assertEqual(report["total"]["recall"], 1.0)


if __name__ == "__main__":
    unittest.main()
