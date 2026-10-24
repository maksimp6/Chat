"""Fail-closed validation paths of the local 3D model editor (#1097)."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

from model_editor import engine
from model_editor.engine import add_primitive, edit_primitive, export_stl, new_scene, validate_scene
from model_editor.store import ModelStore
from model_editor.tools import _store, model_addcube
from storage import LocalDirectoryStorage
from tool_providers.registry import ToolRegistry

MODEL_ID = "6f1c2a8e-3f0b-4c55-9a4e-2b7d6c1e9f00"
PARENT = "a" * 64


def cube(size: float = 10.0) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "type": "cube",
        "dimensions": {"size_mm": size},
        "translation_mm": [0.0, 0.0, 0.0],
        "rotation_deg": [0.0, 0.0, 0.0],
        "scale": [1.0, 1.0, 1.0],
    }


def scene_with(*objects: dict) -> dict:
    scene = new_scene(MODEL_ID, "Validation")
    scene["objects"] = list(objects)
    return scene


class EngineValidationTests(unittest.TestCase):
    def test_name_with_control_characters_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "control characters"):
            new_scene(MODEL_ID, "bad\x07name")

    def test_corrupt_objects_are_rejected(self):
        cylinder = {
            **cube(),
            "type": "cylinder",
            "dimensions": {"diameter_mm": 10.0, "height_mm": 5.0, "segments": 24},
        }
        cases = {
            "invalid scene object": {"id": str(uuid.uuid4())},
            "invalid dimensions": {**cube(), "dimensions": [10.0]},
            "cube requires size_mm": {**cube(), "dimensions": {"diameter_mm": 1.0}},
            "cylinder dimensions are invalid": {**cylinder, "dimensions": {"diameter_mm": 1.0}},
            "segments must be an integer": {
                **cylinder,
                "dimensions": {"diameter_mm": 10.0, "height_mm": 5.0, "segments": True},
            },
            "unsupported primitive type": {**cube(), "type": "sphere"},
        }
        for message, obj in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_scene(scene_with(obj))

    def test_corrupt_scene_documents_are_rejected(self):
        duplicate = cube()
        cases = {
            "invalid scene document": {"schema_version": 1},
            "unsupported scene version": {**scene_with(), "schema_version": 2},
            "invalid parent revision": {**scene_with(), "parent_revision": "XYZ"},
            "scene object limit exceeded": {**scene_with(), "objects": "not-a-list"},
            "duplicate object ID": scene_with(duplicate, copy.deepcopy(duplicate)),
        }
        for message, scene in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_scene(scene)

    def test_triangle_budget_is_enforced(self):
        with (
            patch.object(engine, "MAX_TRIANGLES", 12),
            self.assertRaisesRegex(ValueError, "triangle count limit exceeded"),
        ):
            validate_scene(scene_with(cube(), cube()))

    def test_add_primitive_refuses_a_full_scene(self):
        full = scene_with(*(cube() for _ in range(engine.MAX_OBJECTS)))
        with self.assertRaisesRegex(ValueError, "scene object limit exceeded"):
            add_primitive(full, "cube", {"size_mm": 5.0}, parent_revision=PARENT)

    def test_edit_of_unknown_object_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "object not found in revision"):
            edit_primitive(
                scene_with(cube()),
                parent_revision=PARENT,
                object_id=str(uuid.uuid4()),
                operation="delete",
            )

    def test_export_refuses_empty_and_degenerate_geometry(self):
        with self.assertRaisesRegex(ValueError, "cannot export empty model"):
            export_stl(scene_with())
        flat = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0))
        with (
            patch.object(engine, "triangles", return_value=[flat]),
            self.assertRaisesRegex(ValueError, "degenerate triangle"),
        ):
            export_stl(scene_with(cube()))


class FlakyStorage(LocalDirectoryStorage):
    """Returns different bytes than were written, like a corrupted backend."""

    def download(self, key, credentials=None):
        return super().download(key, credentials=credentials) + b" "


class StoreValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        self.store = ModelStore(LocalDirectoryStorage(self.root), "alice")

    def test_owner_and_runtime_scope_are_required(self):
        provider = LocalDirectoryStorage(self.root)
        with self.assertRaisesRegex(ValueError, "trusted owner identity"):
            ModelStore(provider, " ")
        with self.assertRaisesRegex(ValueError, "runtime scope"):
            ModelStore(provider, "alice", "")

    def test_identifiers_are_validated(self):
        with self.assertRaisesRegex(ValueError, "model_id must be a UUID"):
            self.store.load(123, PARENT)
        with self.assertRaisesRegex(ValueError, "revision must be a sha256"):
            self.store.load(MODEL_ID, "not-a-revision")

    def _put(self, model_id: str, raw: bytes) -> str:
        import hashlib

        revision = hashlib.sha256(raw).hexdigest()
        self.store.provider.upload(self.store._key(model_id, revision), raw, credentials=None)
        return revision

    def test_stored_documents_must_be_valid_objects(self):
        for raw in (b"[1, 2]", b"{not json", b"\xff\xfe"):
            with (
                self.subTest(raw=raw),
                self.assertRaisesRegex(ValueError, "stored model is invalid"),
            ):
                self.store.load(MODEL_ID, self._put(MODEL_ID, raw))

    def test_stored_model_identity_must_match_its_key(self):
        other = str(uuid.uuid4())
        raw = json.dumps(new_scene(other, "Other"), sort_keys=True).encode()
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.store.load(MODEL_ID, self._put(MODEL_ID, raw))

    def test_storage_verification_failures_are_raised(self):
        flaky = ModelStore(FlakyStorage(self.root), "alice")
        with self.assertRaisesRegex(OSError, "model storage verification failed"):
            flaky.save(new_scene(MODEL_ID, "Flaky"))

        scene, _ = add_primitive(
            new_scene(MODEL_ID, "Export"), "cube", {"size_mm": 5.0}, parent_revision=PARENT
        )
        revision = self.store.save(scene)

        class ExportOnlyFlaky(LocalDirectoryStorage):
            def download(self, key, credentials=None):
                data = super().download(key, credentials=credentials)
                return data + b" " if key.endswith(".stl") else data

        flaky_export = ModelStore(ExportOnlyFlaky(self.root), "alice")
        with self.assertRaisesRegex(OSError, "STL storage verification failed"):
            flaky_export.export(scene, revision)


class ToolIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.dict(os.environ, {"ALICE_STORAGE_LOCAL_ROOT": directory.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_falls_back_to_trusted_owner_and_fails_closed_without_one(self):
        with patch("economy.treasury_identity.get_current_owner_id", return_value="owner-1"):
            self.assertIsInstance(_store({}), ModelStore)
        with (
            patch("economy.treasury_identity.get_current_owner_id", return_value=None),
            self.assertRaisesRegex(ValueError, "trusted owner identity"),
        ):
            _store({})

    def test_addcube_validates_translation_before_loading(self):
        class Caller:
            user_id = "alice"
            metadata = {"runtime_id": "host"}

        cfg = {"_universal_context": {"call": Caller()}}
        with self.assertRaisesRegex(ValueError, "translation_mm"):
            model_addcube({"translation_mm": [0, 0, 5000]}, cfg)
        placed = model_addcube({"translation_mm": [10, 0, 0]}, cfg)
        self.assertEqual(placed["object_count"], 1)


class RegistryTests(unittest.TestCase):
    def test_registry_survives_missing_model_editor(self):
        with (
            patch.dict(sys.modules, {"model_editor.tools": None}),
            self.assertLogs("tool_registry", level="ERROR") as logs,
        ):
            registry = ToolRegistry()
        self.assertTrue(any("Model Editor" in line for line in logs.output))
        self.assertIsNotNone(registry)


if __name__ == "__main__":
    unittest.main()
