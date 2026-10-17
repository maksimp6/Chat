import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "check_filenames", ROOT / "scripts" / "check_filenames.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize(
    "path", ["app.py", "docs/agents/guide.md", "economy/billing_v2.py", "a-b/c.d"]
)
def test_plain_ascii_paths_are_allowed(path):
    assert module.find_bad_paths([path]) == []


@pytest.mark.parametrize(
    "path",
    ["my file.py", "tab\there.py", "naïve.py", "emoji😀.md", "back\\slash.py", "new\nline.py"],
)
def test_whitespace_control_and_non_ascii_paths_are_denied(path):
    assert module.find_bad_paths([path]) == [path]


def test_repository_has_no_bad_tracked_paths():
    assert module.find_bad_paths(module.tracked_paths()) == []
