"""Reject pytest runs outside a GitHub Actions runner job.

This file deliberately performs no tests or network operations.
"""
import os


def _on_github_runner():
    return (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("CI") == "true"
        and bool(os.environ.get("GITHUB_RUN_ID"))
        and bool(os.environ.get("RUNNER_NAME"))
        and bool(os.environ.get("RUNNER_TEMP"))
    )


if not _on_github_runner():
    raise RuntimeError(
        "LOCAL_TESTS_FORBIDDEN: code tests must run in a GitHub Actions runner job."
    )
