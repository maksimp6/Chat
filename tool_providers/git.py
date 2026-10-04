import time
import subprocess
import os
import logging
import threading
from functools import wraps

logger = logging.getLogger("git_mcp")

_read_lock = threading.RLock()
_write_lock = threading.RLock()


def read_op(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        with _read_lock:
            return func(*args, **kwargs)

    return wrapper


def write_op(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        with _write_lock:
            with _read_lock:
                return func(*args, **kwargs)

    return wrapper


def _resolve_repo_path(repo_name: str = None, cfg: dict = None) -> str:
    cfg = cfg or {}
    base_dir = cfg.get("repos_base_dir") or "/sdcard/repo"
    target = repo_name or cfg.get("active_repo") or cfg.get("repo_path") or "alice_pro"

    if os.path.isabs(target) and os.path.isdir(target):
        return target

    candidate = os.path.join(base_dir, target)
    if os.path.isdir(candidate):
        return candidate

    for fallback in ["/sdcard/repo", "/sdcard", os.getcwd()]:
        alt = os.path.join(fallback, target)
        if os.path.isdir(alt):
            return alt

    return candidate


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


def _run_git(args: list, cfg: dict = None, repo_name: str = None, is_read: bool = False) -> dict:
    cfg = cfg or {}
    repo_path = _resolve_repo_path(repo_name, cfg)
    timeout = cfg.get("timeout", 25)

    if not os.path.isdir(repo_path):
        return {"success": False, "error": f"Каталог не найден: {repo_path}"}

    is_bare = _is_bare_repo(repo_path)
    if not is_bare and not os.path.isdir(os.path.join(repo_path, ".git")):
        if not (args and args[0] == "init"):
            return {"success": False, "error": f"В каталоге {repo_path} нет .git"}

    lock_file = (
        os.path.join(repo_path, "index.lock")
        if is_bare
        else os.path.join(repo_path, ".git", "index.lock")
    )
    if os.path.exists(lock_file):
        try:
            if time.time() - os.path.getmtime(lock_file) > 2.0:
                os.remove(lock_file)
                time.sleep(0.05)
        except Exception:
            pass

    cmd = ["git"]
    if is_read:
        cmd.append("--no-optional-locks")
    cmd.extend(["-C", repo_path] + args)

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        rel_name = os.path.basename(repo_path)
        out = res.stdout.strip()
        err = res.stderr.strip()

        # Если статус коммита "nothing to commit" — это не фатальный сбой, а информационный ответ
        if res.returncode != 0 and ("nothing to commit" in out or "nothing to commit" in err):
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
        else:
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
        head = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cfg, repo_path, is_read=True)
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
    return _run_git(status_args, cfg, args.get("repo") or args.get("repo_path"), is_read=True)


@read_op
def git_log(args: dict, cfg: dict) -> dict:
    limit = args.get("limit") or cfg.get("default_log_limit", 5)
    branch = args.get("branch") or "HEAD"
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
    return _run_git(["branch", "-a"], cfg, args.get("repo") or args.get("repo_path"), is_read=True)


# --- Запись ---


@write_op
def git_add(args: dict, cfg: dict) -> dict:
    target = args.get("path") or args.get("files") or "."
    return _run_git(["add", target], cfg, args.get("repo") or args.get("repo_path"), is_read=False)


@write_op
def git_commit(args: dict, cfg: dict) -> dict:
    msg = args.get("message") or "Update via Agent"
    repo = args.get("repo") or args.get("repo_path")
    files = args.get("files") or args.get("path")

    # Автоматическая индексация перед коммитом
    if files:
        target = [files] if isinstance(files, str) else files
        _run_git(["add"] + target, cfg, repo, is_read=False)
    else:
        _run_git(["add", "-A"], cfg, repo, is_read=False)

    time.sleep(0.15)
    return _run_git(["commit", "-m", msg], cfg, repo, is_read=False)


@write_op
def git_remote(args: dict, cfg: dict) -> dict:
    action = args.get("action", "list")
    repo = args.get("repo") or args.get("repo_path")
    if action == "list":
        return _run_git(["remote", "-v"], cfg, repo, is_read=True)
    name = args.get("name", "origin")
    url = args.get("url")
    if action == "add":
        if not url:
            return {"success": False, "error": "URL обязателен"}
        return _run_git(["remote", "add", name, url], cfg, repo, is_read=False)
    elif action == "set_url":
        if not url:
            return {"success": False, "error": "URL обязателен"}
        return _run_git(["remote", "set-url", name, url], cfg, repo, is_read=False)
    elif action == "remove":
        return _run_git(["remote", "remove", name], cfg, repo, is_read=False)
    elif action == "show":
        return _run_git(["remote", "show", name], cfg, repo, is_read=True)
    return {"success": False, "error": f"Неизвестное действие: {action}"}


@write_op
def git_push(args: dict, cfg: dict) -> dict:
    repo = args.get("repo") or args.get("repo_path")
    remote = args.get("remote", "origin")
    branch = args.get("branch") or "master"
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
    remote = args.get("remote", "origin")
    branch = args.get("branch")
    cmd = ["pull", remote]
    if branch:
        cmd.append(branch)
    return _run_git(cmd, cfg, repo, is_read=False)


@write_op
def git_fetch(args: dict, cfg: dict) -> dict:
    repo = args.get("repo") or args.get("repo_path")
    remote = args.get("remote", "origin")
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
            "properties": {"file_path": {"type": "string"}, "repo_path": {"type": "string"}},
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
                "action": {"type": "string", "enum": ["list", "add", "set_url", "remove", "show"]},
                "name": {"type": "string"},
                "url": {"type": "string"},
                "repo_path": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    "git_push": {
        "func": git_push,
        "description": "Отправка изменений в remote (по умолчанию ветка master).",
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
