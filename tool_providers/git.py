"""Git tools: read and write operations on allowed local repositories; names that could be read as options are refused."""

import time
import subprocess
import os
import logging
import re
import threading
from functools import wraps

logger = logging.getLogger("logos.tool_providers.git")

_read_lock = threading.RLock()
_write_lock = threading.RLock()
# One read/write lock pair per repository (by real path): a long push in one
# repository does not hold reads of another. An unresolved repository uses the pair above.
_repo_locks: dict[str, tuple[threading.RLock, threading.RLock]] = {
    "": (_read_lock, _write_lock)
}
_repo_locks_guard = threading.Lock()

# Branch, remote and ref names reach git as arguments. A name that starts with "-" would be read
# as an option (for example --output=... or --receive-pack=...), so only plain names are accepted.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")


def _name(value, label: str) -> str:
    text = str(value or "").strip()
    if not _NAME.fullmatch(text) or ".." in text or text.endswith(("/", ".", ".lock")):
        raise ValueError(f"недопустимое имя {label}: {text!r}")
    return text


def _locks_for(call_args: tuple, call_kwargs: dict) -> tuple:
    """The (read, write) lock pair of the repository a tool call works on."""
    tool_args = call_args[0] if call_args else call_kwargs.get("args")
    cfg = call_args[1] if len(call_args) > 1 else call_kwargs.get("cfg")
    tool_args = tool_args if hasattr(tool_args, "get") else {}
    try:
        repo_path = _resolve_repo_path(
            tool_args.get("repo") or tool_args.get("repo_path"),
            cfg if hasattr(cfg, "get") else None,
        )
    except (TypeError, ValueError):
        repo_path = ""
    key = ""
    if repo_path:
        # One repository, one lock pair, whatever case, symlink or alias names it
        # (/sdcard is case-insensitive).
        try:
            st = os.stat(repo_path)
            key = f"{st.st_dev}:{st.st_ino}"
        except OSError:
            key = os.path.realpath(repo_path)
    with _repo_locks_guard:
        locks = _repo_locks.get(key)
        if locks is None:
            locks = _repo_locks[key] = (threading.RLock(), threading.RLock())
    return locks


def read_op(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        read_lock, _ = _locks_for(args, kwargs)
        with read_lock:
            return func(*args, **kwargs)

    return wrapper


def write_op(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        read_lock, write_lock = _locks_for(args, kwargs)
        with write_lock:
            with read_lock:
                return func(*args, **kwargs)

    return wrapper


PROJECT_DIR = os.path.realpath(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


def _allowed_roots(cfg: dict) -> list[str]:
    # The same roots that relative repository names are searched under.
    base_dir = cfg.get("repos_base_dir") or "/sdcard/repo"
    return [
        os.path.realpath(p) for p in (base_dir, "/sdcard/repo", "/sdcard", os.getcwd())
    ]


def _inside(path: str, roots: list[str]) -> bool:
    real = os.path.realpath(path)
    return any(
        real == root or real.startswith(root.rstrip(os.sep) + os.sep) for root in roots
    )


def _resolve_repo_path(repo_name: str = None, cfg: dict = None) -> str:
    cfg = cfg or {}
    base_dir = cfg.get("repos_base_dir") or "/sdcard/repo"
    target = repo_name or cfg.get("active_repo") or cfg.get("repo_path")
    if not target:
        return ""

    # An absolute path is used only inside the repository roots; anywhere else is refused.
    if os.path.isabs(target):
        if os.path.isdir(target) and _inside(target, _allowed_roots(cfg)):
            return target
        return ""

    # A relative name such as "../x" can leave the roots, so every candidate is checked.
    roots = _allowed_roots(cfg)
    candidate = os.path.join(base_dir, target)
    if os.path.isdir(candidate) and _inside(candidate, roots):
        return candidate

    for fallback in ["/sdcard/repo", "/sdcard", os.getcwd()]:
        alt = os.path.join(fallback, target)
        if os.path.isdir(alt) and _inside(alt, roots):
            return alt

    return candidate if _inside(candidate, roots) else ""


def _is_bare_repo(repo_path: str) -> bool:
    if not os.path.isdir(repo_path):
        return False
    if (
        os.path.isfile(os.path.join(repo_path, "HEAD"))
        and os.path.isdir(os.path.join(repo_path, "objects"))
        and not os.path.isdir(os.path.join(repo_path, ".git"))
    ):
        return True
    try:
        res = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "--is-bare-repository"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return res.stdout.strip() == "true"
    except Exception:
        return False


def _git_process_in(repo_path: str) -> bool:
    """True when a running git process works inside this repository (read from /proc).

    Where /proc cannot be read, no process is seen.
    """
    root = os.path.realpath(repo_path)
    try:
        pids = [entry for entry in os.listdir("/proc") if entry.isdigit()]
    except OSError:
        return False
    for pid in pids:
        try:
            with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as fh:
                comm = fh.read().strip()
            if comm != "git" and not comm.startswith("git-"):
                continue
            cwd = os.path.realpath(os.readlink(f"/proc/{pid}/cwd"))
        except OSError:
            continue
        if cwd == root or cwd.startswith(root.rstrip(os.sep) + os.sep):
            return True
    return False


def _run_git(
    args: list, cfg: dict = None, repo_name: str = None, is_read: bool = False
) -> dict:
    cfg = cfg or {}
    repo_path = _resolve_repo_path(repo_name, cfg)
    timeout = cfg.get("timeout", 25)

    if not repo_path:
        return {
            "success": False,
            "error": "Репозиторий не задан: укажите repo или repo_path",
        }
    if not os.path.isdir(repo_path):
        return {"success": False, "error": f"Каталог не найден: {repo_path}"}

    is_bare = _is_bare_repo(repo_path)
    # Linked worktrees store Git metadata in a .git file; Git validates its target.
    if not is_bare and not os.path.exists(os.path.join(repo_path, ".git")):
        if not (args and args[0] == "init"):
            return {"success": False, "error": f"В каталоге {repo_path} нет .git"}

    lock_file = (
        os.path.join(repo_path, "index.lock")
        if is_bare
        else os.path.join(repo_path, ".git", "index.lock")
    )
    if not is_read and os.path.exists(lock_file):
        try:
            old = time.time() - os.path.getmtime(lock_file) > 2.0
            # A lock held by a running git (a long add or commit) is not stale.
            if old and not _git_process_in(repo_path):
                os.remove(lock_file)
                time.sleep(0.05)
        except Exception as exc:
            logger.debug("[GIT] lock cleanup skipped: %s", exc)

    cmd = ["git"]
    if is_read:
        cmd.append("--no-optional-locks")
    cmd.extend(["-C", repo_path] + args)

    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        rel_name = os.path.basename(repo_path)
        out = res.stdout.strip()
        err = res.stderr.strip()

        # Если статус коммита "nothing to commit" — это не фатальный сбой, а информационный ответ
        if res.returncode != 0 and (
            "nothing to commit" in out or "nothing to commit" in err
        ):
            return {
                "success": True,
                "repo": rel_name,
                "output": "Нечего коммитить, рабочая директория чиста.",
                "is_bare": is_bare,
            }

        if res.returncode == 0:
            return {
                "success": True,
                "repo": rel_name,
                "output": out or "(успешно)",
                "is_bare": is_bare,
            }
        return {
            "success": False,
            "repo": rel_name,
            "error": err or out or f"Код: {res.returncode}",
            "is_bare": is_bare,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# --- Чтение ---


@read_op
def git_status(args: dict, cfg: dict) -> dict:
    repo_path = _resolve_repo_path(args.get("repo") or args.get("repo_path"), cfg)
    if _is_bare_repo(repo_path) and not args.get("work_tree"):
        branches = _run_git(["branch", "-a"], cfg, repo_path, is_read=True)
        head = _run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"], cfg, repo_path, is_read=True
        )
        return {
            "success": True,
            "is_bare": True,
            "repo": os.path.basename(repo_path),
            "head_branch": head.get("output"),
            "branches": branches.get("output", "").splitlines(),
            "output": f"Bare-репозиторий (HEAD -> {head.get('output', 'unknown')}). Ветки:\n{branches.get('output', '')}",
        }
    status_args = ["status", "--short", "--branch"]
    if args.get("work_tree"):
        status_args = ["--work-tree", args["work_tree"]] + status_args
    return _run_git(
        status_args, cfg, args.get("repo") or args.get("repo_path"), is_read=True
    )


@read_op
def git_log(args: dict, cfg: dict) -> dict:
    limit = args.get("limit") or cfg.get("default_log_limit", 5)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return {"success": False, "error": f"limit должен быть целым числом: {limit!r}"}
    try:
        branch = _name(args.get("branch") or "HEAD", "branch")
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return _run_git(
        ["log", f"-n{limit}", "--oneline", branch],
        cfg,
        args.get("repo") or args.get("repo_path"),
        is_read=True,
    )


@read_op
def git_diff(args: dict, cfg: dict) -> dict:
    cmd = ["diff", "HEAD"]
    if args.get("file_path"):
        cmd.extend(["--", args["file_path"]])
    return _run_git(cmd, cfg, args.get("repo") or args.get("repo_path"), is_read=True)


@read_op
def git_branches(args: dict, cfg: dict) -> dict:
    return _run_git(
        ["branch", "-a"], cfg, args.get("repo") or args.get("repo_path"), is_read=True
    )


# --- Запись ---


@write_op
def git_add(args: dict, cfg: dict) -> dict:
    target = args.get("path") or args.get("files") or "."
    # A list of paths is accepted here as in git_commit.
    targets = list(target) if isinstance(target, (list, tuple)) else [target]
    # "--" ends the options, so a path can never be read as one.
    return _run_git(
        ["add", "--"] + targets,
        cfg,
        args.get("repo") or args.get("repo_path"),
        is_read=False,
    )


@write_op
def git_commit(args: dict, cfg: dict) -> dict:
    msg = args.get("message") or "Update via Agent"
    repo = args.get("repo") or args.get("repo_path")
    files = args.get("files") or args.get("path")

    # Автоматическая индексация перед коммитом
    if files:
        target = [files] if isinstance(files, str) else files
        add_res = _run_git(["add", "--"] + target, cfg, repo, is_read=False)
        # Only the named paths are committed, not everything else already staged.
        commit_cmd = ["commit", "-m", msg, "--"] + target
    else:
        add_res = _run_git(["add", "-A"], cfg, repo, is_read=False)
        commit_cmd = ["commit", "-m", msg]
    if not add_res.get("success"):
        return add_res

    time.sleep(0.15)
    return _run_git(commit_cmd, cfg, repo, is_read=False)


@write_op
def git_remote(args: dict, cfg: dict) -> dict:
    action = args.get("action", "list")
    repo = args.get("repo") or args.get("repo_path")
    if action == "list":
        return _run_git(["remote", "-v"], cfg, repo, is_read=True)
    try:
        name = _name(args.get("name", "origin"), "remote")
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    url = str(args.get("url") or "").strip()
    if action in {"add", "set_url"}:
        if not url:
            return {"success": False, "error": "URL обязателен"}
        if url.startswith("-"):
            return {"success": False, "error": "URL не может начинаться с '-'"}
    if action == "add":
        return _run_git(["remote", "add", name, url], cfg, repo, is_read=False)
    if action == "set_url":
        return _run_git(["remote", "set-url", name, url], cfg, repo, is_read=False)
    if action == "remove":
        return _run_git(["remote", "remove", name], cfg, repo, is_read=False)
    if action == "show":
        return _run_git(["remote", "show", name], cfg, repo, is_read=True)
    return {"success": False, "error": f"Неизвестное действие: {action}"}


@write_op
def git_push(args: dict, cfg: dict) -> dict:
    repo = args.get("repo") or args.get("repo_path")
    try:
        remote = _name(args.get("remote", "origin"), "remote")
        # Without an explicit branch push the current one; never default to a protected branch.
        branch = _name(args.get("branch") or "HEAD", "branch")
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    cmd = ["push"]
    if args.get("set_upstream"):
        cmd.append("-u")
    if args.get("mirror"):
        cmd.append("--mirror")
    cmd.append(remote)
    if not args.get("mirror"):
        cmd.append(branch)
    return _run_git(cmd, cfg, repo, is_read=False)


@write_op
def git_pull(args: dict, cfg: dict) -> dict:
    repo = args.get("repo") or args.get("repo_path")
    try:
        remote = _name(args.get("remote", "origin"), "remote")
        branch = _name(args["branch"], "branch") if args.get("branch") else None
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    cmd = ["pull", remote]
    if branch:
        cmd.append(branch)
    return _run_git(cmd, cfg, repo, is_read=False)


@write_op
def git_fetch(args: dict, cfg: dict) -> dict:
    repo = args.get("repo") or args.get("repo_path")
    try:
        remote = _name(args.get("remote", "origin"), "remote")
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    cmd = ["fetch", remote]
    if args.get("prune"):
        cmd.append("--prune")
    return _run_git(cmd, cfg, repo, is_read=False)


TOOL_REGISTRY = {
    "git_status": {
        "func": git_status,
        "description": "Получить статус изменений или информацию о bare-репозитории.",
        "parameters": {
            "type": "object",
            "properties": {"repo_path": {"type": "string"}},
            "required": [],
        },
    },
    "git_log": {
        "func": git_log,
        "description": "Получить историю коммитов репозитория.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "branch": {"type": "string"},
                "repo_path": {"type": "string"},
            },
            "required": [],
        },
    },
    "git_diff": {
        "func": git_diff,
        "description": "Получить diff изменений.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "repo_path": {"type": "string"},
            },
            "required": [],
        },
    },
    "git_branches": {
        "func": git_branches,
        "description": "Получить список веток репозитория.",
        "parameters": {
            "type": "object",
            "properties": {"repo_path": {"type": "string"}},
            "required": [],
        },
    },
    "git_add": {
        "func": git_add,
        "description": "Индексировать файлы.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "repo_path": {"type": "string"}},
            "required": [],
        },
    },
    "git_commit": {
        "func": git_commit,
        "description": "Зафиксировать коммит с сообщением. Автоматически индексирует изменения перед коммитом.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Текст сообщения коммита"},
                "files": {
                    "type": "string",
                    "description": "Путь к файлу или пусто для всех изменений",
                },
                "repo_path": {"type": "string"},
            },
            "required": ["message"],
        },
    },
    "git_remote": {
        "func": git_remote,
        "description": "Управление remotes (list, add, set_url, remove, show).",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "add", "set_url", "remove", "show"],
                },
                "name": {"type": "string"},
                "url": {"type": "string"},
                "repo_path": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    "git_push": {
        "func": git_push,
        "description": "Отправка изменений в remote (по умолчанию текущая ветка).",
        "parameters": {
            "type": "object",
            "properties": {
                "remote": {"type": "string"},
                "branch": {"type": "string"},
                "set_upstream": {"type": "boolean"},
                "mirror": {"type": "boolean"},
                "repo_path": {"type": "string"},
            },
        },
    },
    "git_pull": {
        "func": git_pull,
        "description": "Получение и слияние изменений из remote (git pull).",
        "parameters": {
            "type": "object",
            "properties": {
                "remote": {"type": "string"},
                "branch": {"type": "string"},
                "repo_path": {"type": "string"},
            },
        },
    },
    "git_fetch": {
        "func": git_fetch,
        "description": "Получение изменений из remote.",
        "parameters": {
            "type": "object",
            "properties": {
                "remote": {"type": "string"},
                "prune": {"type": "boolean"},
                "repo_path": {"type": "string"},
            },
        },
    },
}


def execute_tool(tool_name: str, arguments: dict, cfg: dict = None) -> dict:
    if tool_name not in TOOL_REGISTRY:
        return {"error": f"Неизвестный инструмент: {tool_name}"}
    tool = TOOL_REGISTRY[tool_name]
    try:
        return tool["func"](arguments or {}, cfg or {})
    except Exception as e:
        logger.exception(f"Ошибка выполнения {tool_name}: {e}")
        return {"error": str(e)}


GIT_TOOLS = TOOL_REGISTRY
