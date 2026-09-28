from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCANNED_PATHS = (
    ROOT / "app.py",
    ROOT / "config.py",
    ROOT / ".env.example",
    ROOT / ".github" / "workflows",
    ROOT / "deploy",
)

SKIP = {Path(__file__).resolve()}


def _files():
    for path in SCANNED_PATHS:
        if path.is_file():
            yield path
            continue
        if path.is_dir():
            for candidate in path.rglob("*"):
                if candidate.is_file() and candidate not in SKIP:
                    yield candidate


def test_runtime_and_deployment_have_no_supabase_dependency():
    offenders = []
    for path in _files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "supabase" in text.lower():
            offenders.append(str(path.relative_to(ROOT)))

    assert not offenders, "Supabase dependency reintroduced: " + ", ".join(sorted(offenders))


def test_supabase_specific_repository_paths_are_removed():
    assert not (ROOT / "supabase").exists()
    assert not (ROOT / "supabase_trace_mirror.py").exists()
    assert not (ROOT / "supabase_startup_check.py").exists()
    assert not (ROOT / "trace_mirror_integration.py").exists()
    assert not (ROOT / ".github" / "workflows" / "supabase-migrations.yml").exists()
