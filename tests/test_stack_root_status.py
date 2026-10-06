import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "stack_root_status.py"
spec = importlib.util.spec_from_file_location("stack_root_status", MODULE_PATH)
stack_root_status = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(stack_root_status)


def test_root_status_cli_uses_current_pr_to_render_root(monkeypatch, capsys):
    from scripts.stacked_pr_topology import PullNode

    monkeypatch.setattr(
        stack_root_status,
        "collect_github_nodes",
        lambda repo: (
            PullNode(877, "root", "master"),
            PullNode(878, "child", "root"),
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["stack_root_status.py", "--repo", "maksimp6/Chat", "--pr", "878"],
    )
    assert stack_root_status.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["root_number"] == 877
    assert payload["blockers"] == ["open descendant #878"]
