"""Stage the existing Alice Pro Python/web runtime for the Android build."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEST = Path(__file__).resolve().parents[1] / "app" / "src" / "main" / "python"
EXCLUDED_ROOTS = {
    ".git",
    ".github",
    ".venv",
    "venv",
    "node_modules",
    "frontend",
    "android",
    "tests",
    "docs",
}
KEEP_FILES = {"android_server.py"}


def clean_staged_backend() -> None:
    for child in DEST.iterdir():
        if child.name not in KEEP_FILES:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()


def stage_python_files() -> None:
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts and rel.parts[0] in EXCLUDED_ROOTS:
            continue
        target = DEST / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def stage_web_assets() -> None:
    for dirname in ("templates", "static"):
        source = ROOT / dirname
        target = DEST / dirname
        if source.exists():
            shutil.copytree(source, target, dirs_exist_ok=True)


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    clean_staged_backend()
    stage_python_files()
    stage_web_assets()
    print(f"Staged Alice Pro runtime into {DEST}")


if __name__ == "__main__":
    main()
