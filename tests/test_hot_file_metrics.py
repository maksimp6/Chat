import json
from pathlib import Path
import subprocess

from scripts.hot_file_metrics import analyze_repository, render_markdown


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _commit(repo: Path, message: str, author_name: str, author_email: str) -> None:
    _git(repo, "add", ".")
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            f"user.name={author_name}",
            "-c",
            f"user.email={author_email}",
            "commit",
            "-m",
            message,
        ],
        check=True,
        capture_output=True,
    )


def test_hot_file_metric_ranks_repeated_source_churn_above_single_touch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    (repo / "stable.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "hot.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_hot.py").write_text("assert True\n", encoding="utf-8")
    _commit(repo, "initial", "Alice", "alice@example.test")

    for index in range(4):
        with (repo / "hot.py").open("a", encoding="utf-8") as handle:
            handle.write(f"value_{index} = {index}\n")
        (repo / "tests" / "test_hot.py").write_text(
            f"assert {index} == {index}\n",
            encoding="utf-8",
        )
        _commit(
            repo,
            f"touch hot {index}",
            "Alice" if index < 2 else "Codex",
            "agent@example.test",
        )

    report = analyze_repository(repo, commits=20)

    assert report["files"][0]["path"] == "hot.py"
    hot = report["files"][0]
    stable = next(row for row in report["files"] if row["path"] == "stable.py")
    assert hot["touches"] == 5
    assert hot["authors"] == 2
    assert hot["score"] > stable["score"]
    assert all(not row["path"].startswith("tests/") for row in report["files"])

    markdown = render_markdown(report, top=1)
    assert "`hot.py`" in markdown
    assert "`stable.py`" not in markdown


def test_hot_file_report_is_json_serializable(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "module.py").write_text("print('ok')\n", encoding="utf-8")
    _commit(repo, "initial", "Alice", "alice@example.test")

    report = analyze_repository(repo, commits=5)

    encoded = json.dumps(report)
    assert '"module.py"' in encoded
    assert report["weights"]["touches"] == 0.30


def test_ci_publishes_hot_file_metrics():
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "Generate hot-file modularity report" in workflow
    assert "python scripts/hot_file_metrics.py" in workflow
    assert "--json-out hot-file-metrics.json" in workflow
    assert "--markdown-out hot-file-metrics.md" in workflow
    assert 'cat hot-file-metrics.md >> "$GITHUB_STEP_SUMMARY"' in workflow
    assert "hot-file-metrics.json" in workflow
    assert "hot-file-metrics.md" in workflow
