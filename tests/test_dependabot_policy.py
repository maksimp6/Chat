"""Dependabot policy (#927): grouped routine updates, visible security updates."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
UPDATES = CONFIG["updates"]


def _directories(update):
    return update.get("directories") or [update["directory"]]


def test_config_is_version_2_with_unique_ecosystems():
    assert CONFIG["version"] == 2
    ecosystems = [update["package-ecosystem"] for update in UPDATES]
    assert len(ecosystems) == len(set(ecosystems))
    assert set(ecosystems) == {"pip", "gradle", "github-actions", "docker", "npm"}


@pytest.mark.parametrize("update", UPDATES, ids=lambda update: update["package-ecosystem"])
def test_every_configured_directory_exists(update):
    assert "directory" not in update or "directories" not in update
    for directory in _directories(update):
        assert (ROOT / directory.lstrip("/")).is_dir(), directory


@pytest.mark.parametrize("update", UPDATES, ids=lambda update: update["package-ecosystem"])
def test_minor_and_patch_are_grouped_and_majors_stay_separate(update):
    groups = update["groups"]
    assert len(groups) == 1
    (group,) = groups.values()
    assert sorted(group["update-types"]) == ["minor", "patch"]
    assert "patterns" not in group and "exclude-patterns" not in group


@pytest.mark.parametrize("update", UPDATES, ids=lambda update: update["package-ecosystem"])
def test_security_updates_are_not_grouped_or_ignored(update):
    for group in update["groups"].values():
        assert group.get("applies-to", "version-updates") == "version-updates"
    # Ignore rules also filter security updates, so blanket ignores are forbidden.
    assert "ignore" not in update


@pytest.mark.parametrize("update", UPDATES, ids=lambda update: update["package-ecosystem"])
def test_updates_are_batched_with_cooldown_and_limits(update):
    assert update["schedule"]["interval"] in {"weekly", "monthly"}
    assert update["cooldown"]["default-days"] >= 7
    assert 0 < update["open-pull-requests-limit"] <= 5
    assert "dependencies" in update["labels"]


def test_every_dockerfile_and_npm_lockfile_is_covered():
    by_ecosystem = {update["package-ecosystem"]: update for update in UPDATES}
    docker_dirs = {d.strip("/") for d in _directories(by_ecosystem["docker"])}
    npm_dirs = {d.strip("/") for d in _directories(by_ecosystem["npm"])}
    skipped = {"node_modules", "android", ".git"}

    def owners(pattern):
        found = set()
        for path in ROOT.rglob(pattern):
            relative = path.relative_to(ROOT)
            if not skipped.intersection(relative.parts):
                found.add("" if relative.parent == Path(".") else relative.parent.as_posix())
        return found

    assert owners("Dockerfile*") <= docker_dirs
    assert owners("package-lock.json") <= npm_dirs


def test_policy_is_documented():
    doc = (ROOT / "docs" / "development" / "dependency-updates.md").read_text(encoding="utf-8")
    assert ".github/dependabot.yml" in doc
    assert "tests/test_dependabot_policy.py" in doc
