from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOVED_VENDOR = "supa" + "base"
REMOVED_PREFIX = "SUPA" + "BASE_"

SCANNED_PATHS = (
    ROOT / "app.py",
    ROOT / "config.py",
    ROOT / ".env.example",
    ROOT / ".github" / "workflows",
    ROOT / "deploy",
)


def _files():
    for path in SCANNED_PATHS:
        if path.is_file():
            yield path
            continue
        if path.is_dir():
            for candidate in path.rglob("*"):
                if candidate.is_file():
                    yield candidate


def test_removed_external_backend_does_not_return():
    offenders = []
    for path in _files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if REMOVED_VENDOR in text.lower() or REMOVED_PREFIX in text:
            offenders.append(str(path.relative_to(ROOT)))

    assert not offenders, "Removed backend dependency reintroduced: " + ", ".join(sorted(offenders))


def test_removed_external_backend_paths_stay_absent():
    assert not (ROOT / REMOVED_VENDOR).exists()
    assert not (ROOT / f"{REMOVED_VENDOR}_trace_mirror.py").exists()
    assert not (ROOT / f"{REMOVED_VENDOR}_startup_check.py").exists()
    assert not (ROOT / "trace_mirror_integration.py").exists()
    assert not (ROOT / ".github" / "workflows" / f"{REMOVED_VENDOR}-migrations.yml").exists()
