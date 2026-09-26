import os
import subprocess
from types import SimpleNamespace

import pytest

import agent_tools


def test_run_git_builds_commands_for_normal_bare_and_worktree(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout=" ok \n", stderr=" warn \n")

    monkeypatch.setattr(agent_tools.subprocess, "run", fake_run)

    normal = tmp_path / "normal"
    normal.mkdir()
    (normal / ".git").mkdir()
    result = agent_tools.ToolExecutor._run_git(["status"], repo_path=str(normal))
    assert result == {"success": True, "stdout": "ok", "stderr": "warn", "exit_code": 0}
    assert calls[-1][0][:3] == ["git", "-C", str(normal)]

    bare = tmp_path / "bare.git"
    bare.mkdir()
    (bare / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    result = agent_tools.ToolExecutor._run_git(
        ["status"], repo_path=str(bare), work_tree="/tmp/work"
    )
    assert result["success"] is True
    assert calls[-1][0][:3] == ["git", "--git-dir", str(bare)]
    assert ["--work-tree", "/tmp/work"] == calls[-1][0][3:5]

    agent_tools.ToolExecutor._run_git(["status"])
    assert calls[-1][0] == ["git", "status"]
    assert calls[-1][1]["timeout"] == 30


def test_run_git_handles_timeout_and_generic_error(monkeypatch):
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("git", 30)),
    )
    assert "Превышено" in agent_tools.ToolExecutor._run_git(["status"])["error"]

    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert agent_tools.ToolExecutor._run_git(["status"]) == {
        "success": False,
        "error": "boom",
    }


def test_is_bare_uses_rev_parse(monkeypatch):
    monkeypatch.setattr(
        agent_tools.ToolExecutor,
        "_run_git",
        classmethod(lambda cls, args, repo_path=".", work_tree=None: {"stdout": "true"}),
    )
    assert agent_tools.ToolExecutor._is_bare("/repo") is True

    monkeypatch.setattr(
        agent_tools.ToolExecutor,
        "_run_git",
        classmethod(lambda cls, args, repo_path=".", work_tree=None: {"stdout": "false"}),
    )
    assert agent_tools.ToolExecutor._is_bare("/repo") is False


def test_git_init_and_clone_build_expected_arguments(monkeypatch, tmp_path):
    calls = []

    def fake_run(cls, args, repo_path=".", work_tree=None):
        calls.append((args, repo_path, work_tree))
        return {"success": True}

    monkeypatch.setattr(agent_tools.ToolExecutor, "_run_git", classmethod(fake_run))

    repo = tmp_path / "repo"
    assert agent_tools.ToolExecutor.git_init(str(repo), bare=True, initial_branch="dev")["success"]
    assert repo.is_dir()
    assert calls[-1][0] == ["init", "--initial-branch=dev", "--bare"]

    agent_tools.ToolExecutor.git_init(str(repo), bare=False)
    assert calls[-1][0] == ["init", "--initial-branch=main"]

    agent_tools.ToolExecutor.git_clone("url", "target", mirror=True)
    assert calls[-1][0] == ["clone", "--mirror", "url", "target"]

    agent_tools.ToolExecutor.git_clone("url", "target", bare=True)
    assert calls[-1][0] == ["clone", "--bare", "url", "target"]

    agent_tools.ToolExecutor.git_clone("url", "target")
    assert calls[-1][0] == ["clone", "url", "target"]


def test_git_status_for_bare_and_worktree(monkeypatch):
    monkeypatch.setattr(
        agent_tools.ToolExecutor,
        "_is_bare",
        classmethod(lambda cls, repo_path: True),
    )
    calls = []

    def fake_run(cls, args, repo_path=".", work_tree=None):
        calls.append((args, repo_path, work_tree))
        if args[:2] == ["branch", "-a"]:
            return {"success": True, "stdout": "main\norigin/main"}
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return {"success": True, "stdout": "main"}
        return {"success": True, "stdout": "## main", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(agent_tools.ToolExecutor, "_run_git", classmethod(fake_run))

    bare = agent_tools.ToolExecutor.git_status("/bare")
    assert bare["is_bare"] is True
    assert bare["head_branch"] == "main"
    assert bare["branches"] == ["main", "origin/main"]

    status = agent_tools.ToolExecutor.git_status("/bare", work_tree="/work")
    assert status["is_bare"] is True
    assert calls[-1] == (["status", "-sb"], "/bare", "/work")


def test_git_status_handles_empty_bare_branch_list(monkeypatch):
    monkeypatch.setattr(
        agent_tools.ToolExecutor,
        "_is_bare",
        classmethod(lambda cls, repo_path: True),
    )

    def fake_run(cls, args, repo_path=".", work_tree=None):
        if args[0] == "branch":
            return {"stdout": ""}
        return {"stdout": "HEAD"}

    monkeypatch.setattr(agent_tools.ToolExecutor, "_run_git", classmethod(fake_run))
    assert agent_tools.ToolExecutor.git_status("/bare")["branches"] == []


def test_git_commit_rejects_bare_without_worktree_and_handles_add_failure(monkeypatch):
    monkeypatch.setattr(
        agent_tools.ToolExecutor,
        "_is_bare",
        classmethod(lambda cls, repo_path: True),
    )
    result = agent_tools.ToolExecutor.git_commit("message", repo_path="/bare")
    assert result["success"] is False
    assert "work_tree" in result["error"]

    monkeypatch.setattr(
        agent_tools.ToolExecutor,
        "_is_bare",
        classmethod(lambda cls, repo_path: False),
    )
    calls = []

    def fail_add(cls, args, repo_path=".", work_tree=None):
        calls.append(args)
        return {"success": False, "stderr": "bad add"}

    monkeypatch.setattr(agent_tools.ToolExecutor, "_run_git", classmethod(fail_add))
    result = agent_tools.ToolExecutor.git_commit("message", ["a.txt"], repo_path="/repo")
    assert result["success"] is False
    assert result["details"]["stderr"] == "bad add"
    assert calls == [["add", "a.txt"]]


def test_git_commit_adds_default_target_and_commits(monkeypatch):
    monkeypatch.setattr(
        agent_tools.ToolExecutor,
        "_is_bare",
        classmethod(lambda cls, repo_path: False),
    )
    calls = []

    def fake_run(cls, args, repo_path=".", work_tree=None):
        calls.append((args, repo_path, work_tree))
        return {"success": True, "stdout": "ok"}

    monkeypatch.setattr(agent_tools.ToolExecutor, "_run_git", classmethod(fake_run))
    result = agent_tools.ToolExecutor.git_commit("message", repo_path="/repo", work_tree="/work")
    assert result["success"] is True
    assert calls[0][0] == ["add", "."]
    assert calls[1][0] == ["commit", "-m", "message"]


@pytest.mark.parametrize(
    ("action", "url", "expected"),
    [
        ("list", None, ["remote", "-v"]),
        (
            "add",
            "https://example/repo.git",
            ["remote", "add", "upstream", "https://example/repo.git"],
        ),
        (
            "set_url",
            "https://example/new.git",
            ["remote", "set-url", "upstream", "https://example/new.git"],
        ),
        ("remove", None, ["remote", "remove", "upstream"]),
        ("show", None, ["remote", "show", "upstream"]),
    ],
)
def test_git_remotes_actions(monkeypatch, action, url, expected):
    calls = []

    def fake_run(cls, args, repo_path=".", work_tree=None):
        calls.append((args, repo_path))
        return {"success": True}

    monkeypatch.setattr(agent_tools.ToolExecutor, "_run_git", classmethod(fake_run))
    assert agent_tools.ToolExecutor.git_remotes(
        action, name="upstream", url=url, repo_path="/repo"
    )["success"]
    assert calls[-1] == (expected, "/repo")


def test_git_remotes_validates_url_and_unknown_action():
    assert agent_tools.ToolExecutor.git_remotes("add") == {
        "success": False,
        "error": "Параметр 'url' обязателен для добавления remote",
    }
    assert agent_tools.ToolExecutor.git_remotes("set_url") == {
        "success": False,
        "error": "Параметр 'url' обязателен для смены URL remote",
    }
    assert "Неизвестное действие" in agent_tools.ToolExecutor.git_remotes("wat")["error"]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, ["push", "origin"]),
        ({"branch": "main"}, ["push", "origin", "main"]),
        ({"mirror": True, "branch": "ignored"}, ["push", "--mirror", "origin"]),
        (
            {"set_upstream": True, "remote": "upstream", "branch": "dev"},
            ["push", "-u", "upstream", "dev"],
        ),
    ],
)
def test_git_push_builds_arguments(monkeypatch, kwargs, expected):
    seen = {}

    def fake_run(cls, args, repo_path=".", work_tree=None):
        seen["args"] = args
        seen["repo_path"] = repo_path
        return {"success": True}

    monkeypatch.setattr(agent_tools.ToolExecutor, "_run_git", classmethod(fake_run))
    agent_tools.ToolExecutor.git_push(repo_path="/repo", **kwargs)
    assert seen == {"args": expected, "repo_path": "/repo"}


def test_git_fetch_and_log_build_arguments(monkeypatch):
    calls = []

    def fake_run(cls, args, repo_path=".", work_tree=None):
        calls.append((args, repo_path))
        return {"success": True}

    monkeypatch.setattr(agent_tools.ToolExecutor, "_run_git", classmethod(fake_run))

    agent_tools.ToolExecutor.git_fetch("upstream", prune=True, repo_path="/repo")
    assert calls[-1] == (["fetch", "upstream", "--prune"], "/repo")

    agent_tools.ToolExecutor.git_fetch(repo_path="/repo")
    assert calls[-1] == (["fetch", "origin"], "/repo")

    agent_tools.ToolExecutor.git_log(3, repo_path="/repo")
    assert calls[-1] == (["log", "-n3", "--oneline", "--decorate"], "/repo")


def test_file_replace_text_missing_not_found_and_success(tmp_path):
    missing = tmp_path / "missing.txt"
    result = agent_tools.ToolExecutor.file_replace_text(str(missing), "a", "b")
    assert result["success"] is False
    assert "не найден" in result["error"]

    path = tmp_path / "file.txt"
    path.write_bytes(b"one\r\ntwo\r\n")
    result = agent_tools.ToolExecutor.file_replace_text(str(path), "absent", "new")
    assert result["success"] is False
    assert "old_text" in result["error"]

    result = agent_tools.ToolExecutor.file_replace_text(str(path), "one\r\ntwo", "alpha\r\nbeta")
    assert result == {"success": True, "file": str(path)}
    assert path.read_text(encoding="utf-8") == "alpha\nbeta\n"


def test_file_replace_text_handles_io_error(monkeypatch, tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("hello", encoding="utf-8")

    monkeypatch.setattr(
        "builtins.open",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    result = agent_tools.ToolExecutor.file_replace_text(str(path), "hello", "bye")
    assert result == {"success": False, "error": "denied"}


def test_shell_execute_success_timeout_and_exception(monkeypatch):
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=" out \n",
            stderr=" err \n",
        ),
    )
    assert agent_tools.ToolExecutor.shell_execute("echo hi") == {
        "success": True,
        "stdout": "out",
        "stderr": "err",
        "exit_code": 0,
    }

    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("command", 20)),
    )
    assert "лимит времени" in agent_tools.ToolExecutor.shell_execute("sleep")["error"]

    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert agent_tools.ToolExecutor.shell_execute("bad") == {
        "success": False,
        "error": "boom",
    }


def test_dispatch_tool_valid_invalid_parameters_and_missing(monkeypatch):
    monkeypatch.setattr(
        agent_tools.ToolExecutor,
        "git_log",
        classmethod(lambda cls, count=5, repo_path=".": {"success": True, "count": count}),
    )
    assert agent_tools.dispatch_tool("git_log", {"count": 2}) == {
        "success": True,
        "count": 2,
    }

    invalid = agent_tools.dispatch_tool("git_log", {"unknown": True})
    assert invalid["success"] is False
    assert "Неверные параметры" in invalid["error"]

    missing = agent_tools.dispatch_tool("does_not_exist")
    assert missing["success"] is False
    assert "не найден" in missing["error"]
