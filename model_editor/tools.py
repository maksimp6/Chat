"""Alice Pro universal local tools for editable 3D primitive scenes.

The tools never accept a filesystem path, owner ID, arbitrary Python, or printer
commands. Scene edits produce new immutable versions; no auto-print or upload.
"""

from __future__ import annotations

from typing import Any

from storage import storage_provider_from_env

from .engine import add_primitive, edit_primitive, number, vector3
from .store import ModelStore


def _store(cfg: dict[str, Any] | None = None) -> ModelStore:
    context = (cfg or {}).get("_universal_context") or {}
    call = context.get("call") if isinstance(context, dict) else None
    user_id = getattr(call, "user_id", None)
    if user_id and str(user_id).strip():
        owner = str(user_id).strip()
    else:
        # Same trusted identity fallback used by 3D business tools.
        from treasury_identity import get_current_owner_id

        resolved_owner = get_current_owner_id(required=True)
        if resolved_owner is None:
            raise ValueError("trusted owner identity is required")
        owner = resolved_owner
    metadata = getattr(call, "metadata", {}) if call is not None else {}
    runtime_id = metadata.get("runtime_id", "host") if isinstance(metadata, dict) else "host"
    return ModelStore(storage_provider_from_env(), owner, str(runtime_id))


def _load_or_create(
    store: ModelStore,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    model_id, revision = arguments.get("model_id"), arguments.get("revision")
    if model_id is None and revision is None:
        return store.create()
    if model_id is None or revision is None:
        raise ValueError("model_id and revision must be provided together")
    return store.load(model_id, revision), str(revision)


def _saved_result(
    store: ModelStore,
    scene: dict[str, Any],
    *,
    object_id: str | None = None,
) -> dict[str, Any]:
    revision = store.save(scene)
    summary = store.summary(scene, revision)
    summary["artifact"] = store.export(scene, revision) if scene["objects"] else None
    if object_id is not None:
        summary["object_id"] = object_id
    return summary


def model_create(arguments: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    store = _store(cfg)
    scene, rev = store.create(
        "Untitled model" if arguments.get("name") is None else arguments["name"]
    )
    return store.summary(scene, rev)


def model_addcube(arguments: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create or extend an editable scene; default cube is 20 mm."""
    store = _store(cfg)
    size = number(
        arguments.get("size_mm") if arguments.get("size_mm") is not None else 20,
        "size_mm",
        0.4,
        500,
    )
    if arguments.get("translation_mm") is not None:
        vector3(arguments["translation_mm"], "translation_mm", -1000, 1000)
    scene, rev = _load_or_create(store, arguments)
    updated, object_id = add_primitive(
        scene,
        "cube",
        {"size_mm": size},
        parent_revision=rev,
        translation_mm=arguments.get("translation_mm"),
    )
    return _saved_result(store, updated, object_id=object_id)


def model_addcylinder(
    arguments: dict[str, Any], cfg: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Create a bounded printable cylindrical primitive."""
    store = _store(cfg)
    diameter = number(
        arguments.get("diameter_mm") if arguments.get("diameter_mm") is not None else 20,
        "diameter_mm",
        0.4,
        500,
    )
    height = number(
        arguments.get("height_mm") if arguments.get("height_mm") is not None else 20,
        "height_mm",
        0.4,
        500,
    )
    segments = arguments.get("segments")
    if segments is None:
        segments = 64
    if isinstance(segments, bool) or not isinstance(segments, int) or not 12 <= segments <= 128:
        raise ValueError("segments must be an integer from 12 to 128")
    if arguments.get("translation_mm") is not None:
        vector3(arguments["translation_mm"], "translation_mm", -1000, 1000)
    scene, rev = _load_or_create(store, arguments)
    updated, object_id = add_primitive(
        scene,
        "cylinder",
        {"diameter_mm": diameter, "height_mm": height, "segments": segments},
        parent_revision=rev,
        translation_mm=arguments.get("translation_mm"),
    )
    return _saved_result(store, updated, object_id=object_id)


def model_edit(arguments: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Non-destructive move/rotate/scale/delete of one object in a scene."""
    store = _store(cfg)
    scene = store.load(arguments["model_id"], arguments["revision"])
    updated = edit_primitive(
        scene,
        parent_revision=arguments["revision"],
        object_id=arguments["object_id"],
        operation=arguments["operation"],
        values=arguments.get("values"),
    )
    return _saved_result(store, updated, object_id=arguments["object_id"])


def model_inspect(arguments: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    store = _store(cfg)
    scene = store.load(arguments["model_id"], arguments["revision"])
    return store.summary(scene, arguments["revision"])


def model_export(arguments: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    store = _store(cfg)
    scene = store.load(arguments["model_id"], arguments["revision"])
    return {
        "model_id": scene["model_id"],
        "revision": arguments["revision"],
        "artifact": store.export(scene, arguments["revision"]),
    }


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_ID = {"type": "string", "description": "UUID of an existing model."}
_REV = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "description": "Immutable revision SHA256.",
}
_POS = {
    "type": "array",
    "items": {"type": "number", "minimum": -1000, "maximum": 1000},
    "minItems": 3,
    "maxItems": 3,
    "description": "World position offset in millimeters, XYZ.",
}
_EXISTING = {"model_id": _ID, "revision": _REV}
_ADD = {
    **_EXISTING,
    "translation_mm": _POS,
}

MODEL_EDITOR_TOOLS: dict[str, dict[str, Any]] = {
    "model.create": {
        "title": "Create 3D model",
        "description": "Создать пустую 3D-сцену Alice. Возвращает model_id и revision; ничего не печатает.",
        "parameters": _schema({"name": {"type": "string", "minLength": 1, "maxLength": 120}}, []),
        "func": model_create,
    },
    "model.addcube": {
        "title": "Add cube to 3D model",
        "description": (
            "Создать куб (по умолчанию 20 мм) в новой или существующей сцене. "
            "Для существующей передай model_id и revision вместе. Сохранить STL, без принтера."
        ),
        "parameters": _schema(
            {
                **_ADD,
                "size_mm": {"type": "number", "minimum": 0.4, "maximum": 500},
            },
            [],
        ),
        "func": model_addcube,
    },
    "model.addcylinder": {
        "title": "Add cylinder to 3D model",
        "description": "Создать цилиндр с диаметром и высотой в мм, в новой или существующей 3D-сцене.",
        "parameters": _schema(
            {
                **_ADD,
                "diameter_mm": {"type": "number", "minimum": 0.4, "maximum": 500},
                "height_mm": {"type": "number", "minimum": 0.4, "maximum": 500},
                "segments": {"type": "integer", "minimum": 12, "maximum": 128},
            },
            [],
        ),
        "func": model_addcylinder,
    },
    "model.edit": {
        "title": "Edit 3D scene",
        "description": (
            "Изменить объект без потери истории: move (сдвиг XYZ мм), rotate (XYZ градусы), "
            "scale (коэффициенты XYZ), delete (без values). Создаёт новую revision и STL."
        ),
        "parameters": _schema(
            {
                **_EXISTING,
                "object_id": _ID,
                "operation": {"type": "string", "enum": ["move", "rotate", "scale", "delete"]},
                "values": {
                    "anyOf": [
                        {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 3,
                            "maxItems": 3,
                        },
                        {"type": "null"},
                    ],
                },
            },
            ["model_id", "revision", "object_id", "operation"],
        ),
        "func": model_edit,
    },
    "model.inspect": {
        "title": "Inspect 3D scene",
        "description": (
            "Прочитать модель, объекты, размер, треугольники и ориентировочную пригодность "
            "для стола A1 mini. Объём суммарный до boolean-операций; посадка не подтверждена."
        ),
        "parameters": _schema(_EXISTING, ["model_id", "revision"]),
        "func": model_inspect,
        "read_only": True,
    },
    "model.export": {
        "title": "Export 3D scene to STL",
        "description": (
            "Экспортировать точную immutable revision модели в бинарный STL через хранилище "
            "Alice (storage_ref и SHA256). Никакого слайсера и загрузки на принтер."
        ),
        "parameters": _schema(_EXISTING, ["model_id", "revision"]),
        "func": model_export,
    },
}
for _definition in MODEL_EDITOR_TOOLS.values():
    _definition.setdefault("capabilities", ["3d", "model", "local"])
    _definition.setdefault("risk_level", "low")
    _definition.setdefault("read_only", False)
    # All writes are owner-scoped immutable scene artifacts, not external effects.
    _definition.setdefault("requires_approval", False)
    _definition.setdefault("executor", {"type": "local"})
    _definition.setdefault("supported_transports", ["responses_api", "local_agent", "mcp"])


__all__ = ["MODEL_EDITOR_TOOLS"]
