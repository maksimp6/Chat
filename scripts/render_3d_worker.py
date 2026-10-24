"""Credential-free bounded subprocess for parsing and rendering mesh bytes."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

if __package__:
    from scripts.render_3d_preview import MAX_SOURCE_BYTES, PreviewError
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from render_3d_preview import MAX_SOURCE_BYTES, PreviewError


def bounded_render(raw: bytes, filename: str) -> tuple[dict, bytes]:
    if not 0 < len(raw) <= MAX_SOURCE_BYTES:
        raise PreviewError("source model exceeds size budget")
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).resolve()), filename],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreviewError("renderer exceeded process/time budget") from exc
    if result.returncode or len(result.stdout) > 2_000_000:
        raise PreviewError("renderer rejected input or exceeded resource budget")
    try:
        payload = json.loads(result.stdout)
        return payload["mesh"], base64.b64decode(payload["png"], validate=True)
    except (ValueError, KeyError, TypeError) as exc:
        raise PreviewError("renderer returned invalid output") from exc


def main() -> int:
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # -I excludes cwd and environment Python hooks; only trusted sibling code is imported.
    from render_3d_preview import load_mesh, render_preview

    try:
        raw = sys.stdin.buffer.read(MAX_SOURCE_BYTES + 1)
        mesh = load_mesh(raw, sys.argv[1])
        png = render_preview(mesh)
        print(
            json.dumps(
                {
                    "mesh": {
                        "sha256": mesh.source_sha256,
                        "triangles": len(mesh.triangles),
                        "minimum": mesh.minimum,
                        "maximum": mesh.maximum,
                        "units": mesh.units,
                    },
                    "png": base64.b64encode(png).decode(),
                }
            )
        )
    except (PreviewError, ValueError, MemoryError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
