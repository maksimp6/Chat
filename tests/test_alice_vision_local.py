"""Local RGB image model and localhost HTTP tests (no external AI calls)."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import error, request

try:
    from PIL import Image, ImageDraw

    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

if _PIL_AVAILABLE:
    from scripts.alice_vision_loopback import LocalVisionHTTP, local_e2e
    from scripts.alice_vision_perturbations import MODES, generate_private_cases
    from scripts.alice_vision_shapes import (
        TRUSTED_LABELS,
        _components,
        load_model,
        model_digest,
        predict,
        save_model,
        train,
        validate_model,
    )
    from scripts.visual_object_contract import load_cases


def _case(name: str, payload: bytes, label: str | None, box: list | None) -> dict:
    return {
        "id": name,
        "image": name + ".png",
        "image_sha256": hashlib.sha256(payload).hexdigest(),
        "source_kind": "cad_render",
        "annotation_status": "reviewed",
        "objects": [{"label": label, "bbox": box}] if label else [],
        "forbidden_labels": ["cutting_spindle", "electric_motor"],
        "forbidden_claims": ["motor_500w", "physically_printed"],
    }


def _png(which: str) -> bytes:
    image = Image.new("RGB", (220, 160), (245, 246, 247))
    draw = ImageDraw.Draw(image)
    if which == "stand":
        draw.polygon(
            [(38, 135), (38, 66), (62, 42), (146, 64), (164, 117), (164, 143)],
            fill=(55, 100, 135),
        )
    elif which == "ring":
        draw.ellipse((55, 30, 169, 148), fill=(155, 95, 53))
        draw.ellipse((83, 62, 140, 114), fill=(245, 246, 247))
    elif which != "blank":
        raise ValueError("invalid synthetic fixture")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _payload(image: bytes) -> bytes:
    return json.dumps(
        {
            "case_id": "opaque-not-a-label",
            "image_base64": base64.b64encode(image).decode("ascii"),
            "image_sha256": hashlib.sha256(image).hexdigest(),
            "mime_type": "image/png",
            "prompt": "Classify pixels; not the file name.",
        }
    ).encode("utf-8")


@unittest.skipUnless(_PIL_AVAILABLE, "focused Pillow-only image tests")
class LocalVisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.gallery = self.root / "gallery"
        self.gallery.mkdir()
        blob_stand = _png("stand")
        blob_ring = _png("ring")
        blob_blank = _png("blank")
        self.gallery.joinpath("stand.png").write_bytes(blob_stand)
        self.gallery.joinpath("ring.png").write_bytes(blob_ring)
        self.gallery.joinpath("blank.png").write_bytes(blob_blank)
        self.cases = [
            _case("stand", blob_stand, "phone_stand", [38 / 220, 42 / 160, 165 / 220, 144 / 160]),
            _case("ring", blob_ring, "retaining_ring", [55 / 220, 30 / 160, 170 / 220, 149 / 160]),
            _case("blank", blob_blank, None, None),
        ]
        self.manifest = self.root / "training.json"
        self.manifest.write_text(json.dumps(self.cases))
        self.model = train(self.manifest, self.gallery)

    def test_train_is_real_pixel_analysis(self):
        labels = [obj["label"] for obj in predict(_png("stand"), self.model)["objects"]]
        self.assertEqual(labels, ["phone_stand"])
        labels = [obj["label"] for obj in predict(_png("ring"), self.model)["objects"]]
        self.assertEqual(labels, ["retaining_ring"])

    def test_blank_requires_abstention(self):
        self.assertEqual(predict(_png("blank"), self.model)["objects"], [])

    def test_color_hidden_by_alpha_is_invisible(self):
        image = Image.new("RGBA", (220, 160), (245, 246, 247, 255))
        ImageDraw.Draw(image).rectangle((20, 20, 190, 140), fill=(40, 110, 200, 0))
        out = io.BytesIO()
        image.save(out, format="PNG")
        self.assertEqual(_components(out.getvalue()), [])

    def test_trusted_label_gate(self):
        self.assertNotIn("cutting_spindle", TRUSTED_LABELS)
        self.model["templates"][0]["outputs"][0]["label"] = "cutting_spindle"
        with self.assertRaisesRegex(ValueError, "unsupported"):
            validate_model(self.model)

    def test_training_rejects_physical_photos(self):
        cases = json.loads(self.manifest.read_text())
        cases[0]["source_kind"] = "physical_photo"
        self.manifest.write_text(json.dumps(cases))
        with self.assertRaisesRegex(ValueError, "approval"):
            train(self.manifest, self.gallery)

    def test_model_is_private_and_hash_pinned(self):
        file = self.root / "local-model.json"
        digest = save_model(self.model, file)
        self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        self.assertEqual(digest, model_digest(load_model(file, expected_sha256=digest)))
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            load_model(file, expected_sha256="0" * 64)
        os.chmod(file, 0o644)
        with self.assertRaisesRegex(ValueError, "publicly readable"):
            load_model(file)

    def test_training_examples_are_never_exported_as_file_paths(self):
        file = self.root / "model.json"
        save_model(self.model, file)
        raw = file.read_text()
        self.assertNotIn(str(self.root), raw)
        self.assertNotIn("image_base64", raw)

    def test_augmentation_cases_are_private_and_pinned(self):
        path = generate_private_cases(self.manifest, self.gallery, self.root / "derived")
        cases = load_cases(path)
        self.assertEqual(len(cases), len(MODES) * 3)
        self.assertEqual({x["augmentation"] for x in cases}, set(MODES))
        for item in cases:
            blob = (path.parent / item["image"]).read_bytes()
            self.assertEqual(hashlib.sha256(blob).hexdigest(), item["image_sha256"])

    def test_in_sample_never_marked_accepted_even_when_reviewed(self):
        report = local_e2e(
            self.manifest,
            self.manifest,
            self.gallery,
            self.root / "result",
            self.root / "local-private-model.json",
        )
        self.assertEqual(report["conclusion"], "IN_SAMPLE_ONLY")
        self.assertEqual(report["status"], "REVIEW_PENDING")
        self.assertEqual(report["total"]["tp"], 2)
        self.assertTrue(report["engine"]["evaluation_leakage_warning"])
        self.assertEqual(report["loopback_timing_ms"]["requests"], 3)

    def test_model_trained_at_different_revision_is_rejected(self):
        model_file = self.root / "old.json"
        save_model(self.model, model_file)
        cases = json.loads(self.manifest.read_text())
        cases[0]["objects"][0]["bbox"] = [0.1, 0.1, 0.9, 0.9]
        self.manifest.write_text(json.dumps(cases))
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            local_e2e(
                self.manifest,
                self.manifest,
                self.gallery,
                self.root / "bad-model",
                model_file,
            )

    def test_explicit_loopback_listener_and_health(self):
        server = LocalVisionHTTP(self.model)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        self.port = server.server_port
        self.opener = request.build_opener(request.ProxyHandler({}))
        with self.opener.open(f"http://127.0.0.1:{self.port}/healthz") as response:
            record = json.loads(response.read())
        self.assertEqual(record["model_sha256"], model_digest(self.model))
        self.assertEqual(record["bind"], "127.0.0.1")
        self.assertEqual(
            self._send(_payload(_png("ring")))["objects"][0]["label"], "retaining_ring"
        )

    def _send(self, blob: bytes, host: str | None = None):
        url = f"http://127.0.0.1:{self.port}/recognize"
        headers = {"Content-Type": "application/json"}
        if host:
            headers["Host"] = host
        req = request.Request(url, data=blob, headers=headers, method="POST")
        with self.opener.open(req) as response:
            return json.loads(response.read())

    def test_invalid_hash_and_host_are_denied(self):
        server = LocalVisionHTTP(self.model)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        self.port = server.server_port
        self.opener = request.build_opener(request.ProxyHandler({}))
        bad = json.loads(_payload(_png("stand")))
        bad["image_sha256"] = "0" * 64
        with self.assertRaises(error.HTTPError) as failure:
            self._send(json.dumps(bad).encode())
        self.assertEqual(failure.exception.code, 400)
        with self.assertRaises(error.HTTPError) as failure:
            self._send(_payload(_png("stand")), host="evil.invalid")
        self.assertEqual(failure.exception.code, 403)

    def test_filesystem_request_fields_fail_closed(self):
        server = LocalVisionHTTP(self.model)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        self.port = server.server_port
        self.opener = request.build_opener(request.ProxyHandler({}))
        bad = json.loads(_payload(_png("stand")))
        bad["file"] = "/etc/passwd"
        with self.assertRaises(error.HTTPError) as failure:
            self._send(json.dumps(bad).encode())
        self.assertEqual(failure.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
