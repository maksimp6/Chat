"""Fail closed on direct imports of the tests package outside runner jobs."""
import os

if not (
    os.environ.get("GITHUB_ACTIONS") == "true"
    and os.environ.get("CI") == "true"
    and os.environ.get("GITHUB_RUN_ID")
    and os.environ.get("RUNNER_NAME")
    and os.environ.get("RUNNER_TEMP")
):
    raise RuntimeError(
        "LOCAL_TESTS_FORBIDDEN: tests may only run in a GitHub Actions runner job."
    )
