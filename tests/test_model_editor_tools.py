"""No-printer, owner-scoped E2E for Alice's local 3D editor."""

from __future__ import annotations

import math
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model_editor.engine import (
    add_primitive,
    export_stl,
    new_scene,
)
from model_editor.store import ModelStore
from model_editor.tools import (
    MODEL_EDITOR_TOOLS,
    model_addcube,
    model_addcylinder,
    model_create,
    model_edit,
    model_export,
    model_inspect,
)
from storage import LocalDirectoryStorage
from tool_registry import ToolRegistry
from universal_tool_platform import UniversalToolCall, UniversalToolExecutor


class Caller:
    def __init__(self, owner: str = "alice", runtime: str = "host") -> None:
        self.user_id = owner
        self.metadata = {"runtime_id": runtime}


class EditorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.dict(os.environ, {"ALICE_STORAGE_LOCAL_ROOT": self.directory.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def cfg(owner: str = "alice", runtime: str = "host") -> dict:
        return {"_universal_context": {"call": Caller(owner, runtime)}}

    def test_cube_create_edit_export_and_immutable_history(self):
        cfg = self.cfg()
        first = model_addcube({}, cfg)
        self.assertEqual(first["object_count"], 1)
        self.assertEqual(first["triangles"], 12)
        self.assertEqual(first["size_mm"], [20, 20, 20])
        self.assertEqual(first["artifact"]["size_bytes"], 684)
        self.assertTrue(first["fits_a1_mini_by_dimensions"])
        second = model_edit(
            {
                "model_id": first["model_id"],
                "revision": first["revision"],
                "object_id": first["object_id"],
                "operation": "move",
                "values": [12, 2, 0],
            },
            cfg,
        )
        self.assertNotEqual(first["revision"], second["revision"])
        original = model_inspect(
            {
                "model_id": first["model_id"],
                "revision": first["revision"],
            },
            cfg,
        )
        self.assertEqual(original["objects"][0]["translation_mm"], [0, 0, 0])
        self.assertEqual(second["objects"][0]["translation_mm"], [12, 2, 0])
        replay = model_edit(
            {
                "model_id": first["model_id"],
                "revision": first["revision"],
                "object_id": first["object_id"],
                "operation": "move",
                "values": [12, 2, 0],
            },
            cfg,
        )
        self.assertEqual(replay["revision"], second["revision"])
        self.assertEqual(replay["artifact"]["sha256"], second["artifact"]["sha256"])
        self.assertEqual(
            model_export(
                {
                    "model_id": second["model_id"],
                    "revision": second["revision"],
                },
                cfg,
            )["artifact"]["sha256"],
            second["artifact"]["sha256"],
        )

    def test_two_primitives_rotate_scale_delete_and_old_version(self):
        cfg = self.cfg()
        first = model_create({"name": "Holder"}, cfg)
        cube = model_addcube(
            {
                "model_id": first["model_id"],
                "revision": first["revision"],
                "size_mm": 30,
            },
            cfg,
        )
        cylinder = model_addcylinder(
            {
                "model_id": cube["model_id"],
                "revision": cube["revision"],
                "diameter_mm": 52,
                "height_mm": 16,
                "segments": 32,
                "translation_mm": [1, 2, 0],
            },
            cfg,
        )
        self.assertEqual(cylinder["object_count"], 2)
        self.assertEqual(cylinder["triangles"], 140)
        turn = model_edit(
            {
                "model_id": cylinder["model_id"],
                "revision": cylinder["revision"],
                "object_id": cylinder["object_id"],
                "operation": "rotate",
                "values": [0, 0, 90],
            },
            cfg,
        )
        scale = model_edit(
            {
                "model_id": turn["model_id"],
                "revision": turn["revision"],
                "object_id": turn["object_id"],
                "operation": "scale",
                "values": [1.1, 1.1, 1],
            },
            cfg,
        )
        self.assertEqual(scale["objects"][1]["scale"], [1.1, 1.1, 1.0])
        deleted = model_edit(
            {
                "model_id": scale["model_id"],
                "revision": scale["revision"],
                "object_id": cube["object_id"],
                "operation": "delete",
            },
            cfg,
        )
        self.assertEqual(deleted["object_count"], 1)
        self.assertEqual(cylinder["object_count"], 2)
        self.assertEqual(
            model_inspect(
                {
                    "model_id": cylinder["model_id"],
                    "revision": cylinder["revision"],
                },
                cfg,
            )["object_count"],
            2,
        )

    def test_owner_and_runtime_isolation(self):
        obj = model_addcube({}, self.cfg("owner-a", "runtime-one"))
        args = {"model_id": obj["model_id"], "revision": obj["revision"]}
        with self.assertRaisesRegex(ValueError, "not found"):
            model_inspect(args, self.cfg("owner-b", "runtime-one"))
        with self.assertRaisesRegex(ValueError, "not found"):
            model_inspect(args, self.cfg("owner-a", "runtime-two"))
        self.assertEqual(model_inspect(args, self.cfg("owner-a", "runtime-one"))["object_count"], 1)

    def test_invalid_dimensions_names_and_path_traversal(self):
        cfg = self.cfg()
        for size in (-1, 0.2, 501, math.nan, math.inf, "12", True):
            with self.subTest(size=size), self.assertRaises(ValueError):
                model_addcube({"size_mm": size}, cfg)
        for args in (
            {"diameter_mm": -1},
            {"diameter_mm": 21, "segments": True},
            {"diameter_mm": 21, "segments": 1000},
            {"diameter_mm": 21, "translation_mm": [1, 2]},
            {"diameter_mm": 21, "translation_mm": ["1", 2, 3]},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                model_addcylinder(args, cfg)
        for name in ("", "\n", "x" * 121):
            with self.subTest(name=name), self.assertRaises(ValueError):
                model_create({"name": name}, cfg)
        for invalid in ("/etc/passwd", "../other-model", "not-a-uuid"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                model_inspect({"model_id": invalid, "revision": "a" * 64}, cfg)

    def test_invalid_edits_do_not_destroy_model(self):
        cfg = self.cfg()
        created = model_addcube({}, cfg)
        base = {
            "model_id": created["model_id"],
            "revision": created["revision"],
            "object_id": created["object_id"],
        }
        with self.assertRaises(ValueError):
            model_edit({**base, "operation": "scale", "values": [-1, 1, 1]}, cfg)
        with self.assertRaises(ValueError):
            model_edit({**base, "operation": "delete", "values": [0, 0, 0]}, cfg)
        with self.assertRaises(ValueError):
            model_edit({**base, "operation": "unsupported"}, cfg)
        with self.assertRaises(ValueError):
            model_addcube({"model_id": created["model_id"]}, cfg)
        self.assertEqual(
            model_inspect(
                {
                    "model_id": created["model_id"],
                    "revision": created["revision"],
                },
                cfg,
            )["object_count"],
            1,
        )

    def test_checksum_detects_corrupt_stored_snapshot(self):
        provider = LocalDirectoryStorage(self.directory.name)
        store = ModelStore(provider, "alice")
        scene, revision = store.create()
        file = Path(provider._root) / store._key(scene["model_id"], revision)
        file.write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "checksum"):
            store.load(scene["model_id"], revision)

    def test_stl_winding_and_manifold_for_primitives(self):
        model = new_scene("00000000-0000-4000-a000-000000000123")
        for kind, dim in (
            ("cube", {"size_mm": 15.0}),
            ("cylinder", {"diameter_mm": 14.0, "height_mm": 18.0, "segments": 48}),
        ):
            scene, _ = add_primitive(model, kind, dim, parent_revision="b" * 64)
            raw = export_stl(scene)
            count = struct.unpack_from("<I", raw, 80)[0]
            self.assertEqual(len(raw), 84 + count * 50)
            edges = {}
            signed_volume = 0.0
            for i in range(count):
                tri = struct.unpack_from("<12fH", raw, 84 + 50 * i)
                points = tuple(tuple(tri[j : j + 3]) for j in (3, 6, 9))
                for a, b in ((0, 1), (1, 2), (2, 0)):
                    edge = tuple(sorted((points[a], points[b])))
                    edges[edge] = edges.get(edge, 0) + 1
                a, b, c = points
                cross = (
                    b[1] * c[2] - b[2] * c[1],
                    b[2] * c[0] - b[0] * c[2],
                    b[0] * c[1] - b[1] * c[0],
                )
                signed_volume += sum(a[j] * cross[j] for j in range(3)) / 6
            self.assertTrue(all(num == 2 for num in edges.values()), kind)
            self.assertGreater(signed_volume, 0, kind)
            self.assertEqual(count, 12 if kind == "cube" else 4 * 48)

    def test_execution_trace_records_editor_tool_call(self):
        class FakeTrace:
            def __init__(self):
                self.trace = {"tool_calls": []}

            def track_tool_execution(self, name, arguments, callback, **kwargs):
                result = callback()
                self.trace["tool_calls"].append(
                    {
                        "name": name,
                        "arguments": dict(arguments),
                        "call_id": kwargs.get("call_id"),
                        "result": result,
                    }
                )
                return result

        trace = FakeTrace()
        registry = ToolRegistry()
        call = UniversalToolCall(
            tool_name="model.addcube",
            arguments={"size_mm": 16},
            user_id="owner-for-trace",
            trace_id="trace-model",
            invocation_id="inv-model",
            call_id="call-model",
            transport="responses_api",
            metadata={"runtime_id": "host"},
        )
        result = UniversalToolExecutor(registry).execute_with_trace(call, trace)
        self.assertTrue(result["success"], result["error"])
        self.assertEqual(trace.trace["tool_calls"][0]["name"], "model.addcube")
        self.assertEqual(trace.trace["tool_calls"][0]["call_id"], "call-model")
        self.assertIn("sha256", result["data"]["artifact"])

    def test_registry_and_universal_executor_approval_boundary(self):
        registry = ToolRegistry()
        names = registry.get_available_categories().get("3d", [])
        self.assertTrue(set(MODEL_EDITOR_TOOLS).issubset(names))
        self.assertTrue(registry.get_tool_meta("model.inspect")["read_only"])
        self.assertFalse(registry.get_tool_meta("model.addcube")["read_only"])
        self.assertFalse(registry.get_tool_meta("model.addcube")["requires_approval"])
        executor = UniversalToolExecutor(registry)
        call = UniversalToolCall(
            tool_name="model.addcube",
            arguments={"size_mm": 20},
            user_id="trusted-owner",
            transport="local_agent",
            trace_id="trace-3d",
            invocation_id="inv-3d",
            metadata={"runtime_id": "host"},
        )
        result = executor.execute(call)
        self.assertTrue(result["success"], result.get("error"))
        self.assertEqual(result["data"]["object_count"], 1)
        self.assertEqual(result["metadata"]["tool"], "model.addcube")
        rejected = executor.execute(
            UniversalToolCall(
                tool_name="model.addcube",
                arguments={"size_mm": 20, "owner_id": "forged"},
                user_id="trusted-owner",
                transport="local_agent",
            )
        )
        self.assertFalse(rejected["success"])
        self.assertEqual(rejected["metadata"]["phase"], "validation")


if __name__ == "__main__":
    unittest.main()
