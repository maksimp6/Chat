from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def test_only_builder_can_write_packages_and_pr_build_is_same_repo_only():
    data = workflow("ci-image.yml")
    assert data["permissions"] == {"contents": "read"}
    writers = [
        name
        for name, job in data["jobs"].items()
        if job.get("permissions", {}).get("packages") == "write"
    ]
    assert writers == ["build", "cache-scenarios"]
    guard = data["jobs"]["build"]["if"]
    assert "github.event.pull_request.head.repo.full_name == github.repository" in guard
    assert "github.event.pull_request.number == 762" in guard
    assert data["jobs"]["benchmark"]["permissions"] == {"contents": "read", "packages": "read"}


def test_ab_uses_one_identical_full_suite_and_keeps_existing_setup_caches():
    benchmark = workflow("ci-image.yml")["jobs"]["benchmark"]
    assert benchmark["strategy"]["matrix"]["method"] == ["setup", "image"]
    assert benchmark["strategy"]["matrix"]["repeat"] == ["1", "2", "3"]
    assert [v["scenario"] for v in benchmark["strategy"]["matrix"]["include"]] == [
        "source",
        "dev-requirements",
        "requirements",
        "npm",
    ]
    steps = benchmark["steps"]
    test_steps = [s for s in steps if s["name"] == "Run identical full SQLite suite with coverage"]
    assert len(test_steps) == 1
    command = test_steps[0]["run"]
    for arg in ["-n auto", "--dist=loadfile", "--cov=.", "--cov-branch", '"${command[@]}"']:
        assert arg in command
    assert 'docker exec alice-ci-benchmark "${command[@]}"' in command
    caches = [s["with"]["cache"] for s in steps if "cache" in s.get("with", {})]
    assert caches == ["pip", "npm"]
    assert any(s["name"] == "Pull immutable CI image" for s in steps)
    assert any(s["name"] == "Start CI container" for s in steps)


def test_scenarios_publish_immutable_variants_without_exporting_mutated_cache_layers():
    scenarios = workflow("ci-image.yml")["jobs"]["cache-scenarios"]
    assert scenarios["needs"] == "build"
    assert scenarios["strategy"]["matrix"]["scenario"] == [
        "cold",
        "warm",
        "source",
        "dev-requirements",
        "requirements",
        "npm",
    ]
    builds = [
        s["with"] for s in scenarios["steps"] if "docker/build-push-action@" in s.get("uses", "")
    ]
    assert len(builds) == 1
    assert builds[0]["cache-from"] == "type=gha,scope=alice-ci-image"
    assert "cache-to" not in builds[0]
    assert builds[0]["push"] == "${{ steps.variant-existing.outputs.found != 'true' }}"
    assert builds[0]["no-cache"] == "${{ matrix.scenario == 'cold' }}"


def test_required_workflows_keep_full_triggers_and_no_image_rollout():
    for name in ["ci.yml", "security.yml", "codeql.yml", "format.yml"]:
        data = workflow(name)
        assert "pull_request" in data["on"]
        trigger = data["on"]["pull_request"]
        if isinstance(trigger, dict):
            assert "paths" not in trigger and "paths-ignore" not in trigger
        assert all("container" not in job for job in data["jobs"].values())
        assert all(
            job.get("permissions", {}).get("packages") != "write" for job in data["jobs"].values()
        )


def test_package_mounts_keep_cached_downloads_out_of_image_and_exclude_source():
    dockerfile = (ROOT / "deploy/ci/Dockerfile").read_text()
    assert "--mount=type=cache,target=/root/.cache/pip" in dockerfile
    assert "--mount=type=cache,target=/root/.npm" in dockerfile
    assert "npm cache clean" not in dockerfile
    assert "COPY . " not in dockerfile
    assert "chrome" not in dockerfile.lower()
    assert "android" not in dockerfile.lower()


def test_ab_toolchain_versions_match_image_versions():
    benchmark = workflow("ci-image.yml")["jobs"]["benchmark"]
    python_step = next(
        s for s in benchmark["steps"] if "actions/setup-python@" in s.get("uses", "")
    )
    node_step = next(s for s in benchmark["steps"] if "actions/setup-node@" in s.get("uses", ""))
    dockerfile = (ROOT / "deploy/ci/Dockerfile").read_text()
    assert f"FROM python:{python_step['with']['python-version']}-slim" in dockerfile
    assert f"ARG NODE_VERSION={node_step['with']['node-version']}" in dockerfile
    assert "nodejs=${NODE_VERSION}-1nodesource1" in dockerfile


def test_setup_arm_uses_the_same_actions_as_required_application_job():
    required_steps = workflow("ci.yml")["jobs"]["backend"]["steps"]
    benchmark_steps = workflow("ci-image.yml")["jobs"]["benchmark"]["steps"]
    for action in ("actions/setup-python@", "actions/setup-node@"):
        required = next(s for s in required_steps if action in s.get("uses", ""))
        experiment = next(s for s in benchmark_steps if action in s.get("uses", ""))
        assert required["uses"] == experiment["uses"]
        assert required["with"]["cache"] == experiment["with"]["cache"]
        assert (
            required["with"]["cache-dependency-path"] == experiment["with"]["cache-dependency-path"]
        )
