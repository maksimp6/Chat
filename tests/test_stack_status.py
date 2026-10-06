import json

from scripts import stack_status


def test_stack_status_resolves_root_and_mermaid(monkeypatch):
    class Result:
        stdout = json.dumps(
            [
                {"number": 877, "state": "open", "head": {"ref": "root"}, "base": {"ref": "master"}},
                {"number": 878, "state": "open", "head": {"ref": "child"}, "base": {"ref": "root"}},
                {"number": 879, "state": "open", "head": {"ref": "leaf"}, "base": {"ref": "child"}},
                {"number": 999, "state": "open", "head": {"ref": "other"}, "base": {"ref": "master"}},
            ]
        )

    monkeypatch.setattr(stack_status.subprocess, "run", lambda *args, **kwargs: Result())
    result = stack_status.collect("maksimp6/Chat", 879)
    assert result["root_pr"] == 877
    assert result["descendants"] == [878, 879]
    assert "P877 --> P878" in result["mermaid"]
    assert "P878 --> P879" in result["mermaid"]
    assert "#999" not in result["mermaid"]
