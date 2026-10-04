from pathlib import Path

from scripts.ci_environment_hash import INPUTS


ROOT = Path(__file__).resolve().parents[1]


def test_ci_environment_hash_covers_toolchain_inputs() -> None:
    assert INPUTS == (
        "deploy/ci/Dockerfile",
        "requirements.txt",
        "requirements-dev.txt",
        "package.json",
    )
    for relative in INPUTS:
        assert (ROOT / relative).is_file()


def test_ci_environment_image_contains_shared_python_and_node_toolchains() -> None:
    dockerfile = (ROOT / "deploy" / "ci" / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.14-slim" in dockerfile
    assert "requirements.txt requirements-dev.txt" in dockerfile
    assert "pip install -r requirements.txt -r requirements-dev.txt" in dockerfile
    assert "COPY package.json" in dockerfile
    assert "npm install --ignore-scripts --no-audit --no-fund --package-lock=false" in dockerfile


def test_content_hash_ignores_application_source_but_tracks_each_environment_input(
    tmp_path, monkeypatch, capsys
):
    from scripts import ci_environment_hash as hashing

    monkeypatch.setattr(hashing, "ROOT", tmp_path)
    for relative in hashing.INPUTS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative)

    def current():
        hashing.main()
        return capsys.readouterr().out.strip()

    baseline = current()
    assert current() == baseline
    (tmp_path / "app.py").write_text('print("new application")')
    assert current() == baseline
    for relative in hashing.INPUTS:
        path = tmp_path / relative
        original = path.read_text()
        path.write_text(original + "\nchanged")
        assert current() != baseline
        path.write_text(original)
    assert current() == baseline
