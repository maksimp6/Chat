#!/usr/bin/env python3
"""Measure #763's registry layer cache without deploying a Chrome service."""

from __future__ import annotations

import argparse
from functools import partial
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.cloudru.registry_client import CloudRuRegistryClient  # noqa: E402
from scripts import cloudru_chrome as chrome  # noqa: E402

SCENARIOS = ("cold", "warm", "python", "worker-js", "dependencies")


def docker_runner(argv, *, cold=False, **kwargs):
    if argv[:2] == ["docker", "build"]:
        argv = [*argv[:2], "--progress=plain", *(["--no-cache"] if cold else []), *argv[2:]]
    return subprocess.run(argv, **kwargs)


def mutate_context(exported, scenario):
    context = exported / "deploy/chrome-worker"
    if scenario == "python":
        # Python deploy code is outside the worker's Docker context.
        with (exported / "scripts/cloudru_chrome.py").open("a") as stream:
            stream.write("\n# source-only cache probe\n")
    elif scenario == "worker-js":
        with (context / "server.mjs").open("a") as stream:
            stream.write("\n// worker-source cache probe\n")
    elif scenario == "dependencies":
        dockerfile = context / "Dockerfile"
        dockerfile.write_text(
            dockerfile.read_text().replace(
                "ca-certificates curl gnupg", "ca-certificates curl gnupg procps"
            )
        )
    return context


def benchmark(root, sha, scenario, registry):
    if chrome.lane() != "test":
        chrome.fail("validation_error")
    chrome.require_reviewed_head(root, sha)
    chrome.prepare_registry(registry)
    with tempfile.TemporaryDirectory(prefix="chrome-cache-probe-") as exported:
        exported = Path(exported)
        chrome.export_source(sha, root, exported)
        context = mutate_context(exported, scenario)
        started = time.perf_counter()
        image = registry.build_and_push(
            registry_name=chrome.REGISTRY,
            repository=chrome.REPOSITORY,
            tag=f"benchmark-{sha}-{scenario}",
            context_dir=str(context),
            dockerfile=str(context / "Dockerfile"),
        )
        print(
            json.dumps(
                {
                    "stage": "chrome_cache_benchmark",
                    "scenario": scenario,
                    "sha": sha,
                    "digest": image.digest,
                    "seconds": time.perf_counter() - started,
                }
            ),
            flush=True,
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    args = parser.parse_args(argv)
    chrome._load_cloudru_credentials()
    registry = CloudRuRegistryClient(runner=partial(docker_runner, cold=args.scenario == "cold"))
    benchmark(Path(__file__).resolve().parents[1], args.sha, args.scenario, registry)


if __name__ == "__main__":
    main()
