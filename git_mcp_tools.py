import time
import subprocess
import os
import logging

logger = logging.getLogger("git_mcp")

def _resolve_repo_path(repo_name: str = None, cfg: dict = None) -> str:
    cfg = cfg or {}
    base_dir = cfg.get("repos_base_dir") or "/sdcard/repo"
    target = repo_name or cfg.get("active_repo") or cfg.get("repo_path") or "alice_pro"

    if os.path.isabs(target) and os.path.isdir(target):
        return target

    candidate = os.path.join(base_dir, target)
    if os.path.isdir(candidate):
        return candidate

    for fallback_base in ["/sdcard/repo", "/sdcard", os.getcwd()]:
        alt = os.path.join(fallback_base, target)
        if os.path.isdir(alt):
            return alt

    return candidate

def _is_bare_repo(repo_path: str) -> bool:
    if not os.path.isdir(repo_path):
        return False
    # В bare репозитории HEAD и objects лежат прямо в корне
    if os.path.isfile(os.path.join(repo_path, "HEAD")) and os.path.isdir(os.path.join(repo_path, "objects")) and not os.path.isdir(os.path.join(repo_path, ".git")):
        return True
    try:
        res = subprocess.run(["git", "-C", repo_path, "rev-parse", "--is-bare-repository"], capture_output=True, text=True, timeout=5)
        return res.stdout.strip() == "true"
    except Exception:
        return False

def _run_git_command(args: list, cfg: dict = None, repo_name: str = None) -> dict:
    cfg = cfg or {}
    repo_path = _resolve_repo_path(repo_name, cfg)
    timeout = cfg.get("timeout", 25)
    allowed_commands = [
        "status", "log", "diff", "branch", "show",
        "add", "commit", "checkout", "remote", "push",
        "pull", "fetch", "init", "rev-parse"
    ]

    if not os.path.isdir(repo_path):
        return {"success": False, "error": f"Папка репозитория не найдена: {repo_path}"}

    is_bare = _is_bare_repo(repo_path)
    if not is_bare and not os.path.isdir(os.path.join(repo_path, ".git")):
        if not (args and args[0] == "init"):
            return {"success": False, "error": f"В папке {repo_path} отсутствует .git и это не bare-репозиторий"}

    lock_file = os.path.join(repo_path, "index.lock") if is_bare else os.path.join(repo_path, ".git", "index.lock")
    wait_start = time.time()
    while os.path.exists(lock_file):
        if time.time() - wait_start > 10:
            try:
                os.remove(lock_file)
            except Exception:
                pass
            break
        time.sleep(0.2)

    if args and args[0] not in allowed_commands:
        return {"success": False, "error": f"Команда не разрешена: {args[0]}"}

    cmd = ["git", "-C", repo_path] + args
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        rel_name = os.path.basename(repo_path)
        if res.returncode == 0:
            return {
                "success": True,
                "repo": rel_name,
                "output": res.stdout.strip() or "(успешно)",
                "is_bare": is_bare
            }
        else:
            return {
                "success": False,
                "repo": rel_name,
                "error": res.stderr.strip() or f"Код: {res.returncode}",
                "is_bare": is_bare
            }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Превышен таймаут ({timeout}с)"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def git_status(args: dict, cfg: dict) -> dict:
    repo_path = _resolve_repo_path(args.get("repo") or args.get("repo_path"), cfg)
    if _is_bare_repo(repo_path) and not args.get("work_tree"):
        branches = _run_git_command(["branch", "-a"], cfg, repo_path)
        head = _run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cfg, repo_path)
        return {
            "success": True,
            "is_bare": True,
            "repo": os.path.basename(repo_path),
            "head_branch": head.get("output"),
            "branches": branches.get("output", "").splitlines(),
            "output": f"Bare-репозиторий (HEAD: {head.get('output', 'unknown')}). Рабочая директория отсутствует."
        }
    status_args = ["status", "--short", "--branch"]
    if args.get("work_tree"):
        status_args = ["--work-tree", args["work_tree"]] + status_args
    return _run_git_command(status_args, cfg, args.get("repo") or args.get("repo_path"))

def git_log(args: dict, cfg: dict) -> dict:
    limit = args.get("limit") or cfg.get("default_log_limit", 5)
    return _run_git_command(["log", f"-n{limit}", "--oneline"], cfg, args.get("repo") or args.get("repo_path"))

def git_diff(args: dict, cfg: dict) -> dict:
    cmd_args = ["diff", "HEAD"]
    if args.get("file_path"):
        cmd_args.extend(["--", args["file_path"]])
    return _run_git_command(cmd_args, cfg, args.get("repo") or args.get("repo_path"))

def git_branches(args: dict, cfg: dict) -> dict:
    return _run_git_command(["branch", "-a"], cfg, args.get("repo") or args.get("repo_path"))

def git_add(args: dict, cfg: dict) -> dict:
    target = args.get("path") or args.get("files") or "."
    return _run_git_command(["add", target], cfg, args.get("repo") or args.get("repo_path"))

def git_commit(args: dict, cfg: dict) -> dict:
    msg = args.get("message") or "Auto-commit via MCP"
    _run_git_command(["add", "."], cfg, args.get("repo") or args.get("repo_path"))
    return _run_git_command(["commit", "-m", msg], cfg, args.get("repo") or args.get("repo_path"))

def git_remote(args: dict, cfg: dict) -> dict:
    action = args.get("action", "list")
    repo = args.get("repo") or args.get("repo_path")
    if action == "list":
        return _run_git_command(["remote", "-v"], cfg, repo)
    name = args.get("name", "origin")
    url = args.get("url")
    if action == "add":
        if not url:
            return {"success": False, "error": "URL обязателен для remote add"}
        return _run_git_command(["remote", "add", name, url], cfg, repo)
    elif action == "set_url":
        if not url:
            return {"success": False, "error": "URL обязателен для remote set-url"}
        return _run_git_command(["remote", "set-url", name, url], cfg, repo)
    elif action == "remove":
        return _run_git_command(["remote", "remove", name], cfg, repo)
    elif action == "show":
        return _run_git_command(["remote", "show", name], cfg, repo)
    return {"success": False, "error": f"Неизвестное действие: {action}"}

def git_push(args: dict, cfg: dict) -> dict:
    repo = args.get("repo") or args.get("repo_path")
    remote = args.get("remote", "origin")
    branch = args.get("branch")
    cmd = ["push"]
    if args.get("set_upstream"):
        cmd.append("-u")
    if args.get("mirror"):
        cmd.append("--mirror")
    cmd.append(remote)
    if branch and not args.get("mirror"):
        cmd.append(branch)
    return _run_git_command(cmd, cfg, repo)

def git_pull(args: dict, cfg: dict) -> dict:
    repo = args.get("repo") or args.get("repo_path")
    remote = args.get("remote", "origin")
    branch = args.get("branch")
    cmd = ["pull", remote]
    if branch:
        cmd.append(branch)
    return _run_git_command(cmd, cfg, repo)

def git_fetch(args: dict, cfg: dict) -> dict:
    repo = args.get("repo") or args.get("repo_path")
    remote = args.get("remote", "origin")
    cmd = ["fetch", remote]
    if args.get("prune"):
        cmd.append("--prune")
    return _run_git_command(cmd, cfg, repo)

TOOL_REGISTRY = {
    "git_status": {
        "func": git_status,
        "description": "Получить статус изменений или информацию о bare-репозитории.",
        "parameters": {"type": "object", "properties": {"repo_path": {"type": "string"}}, "required": []}
    },
    "git_log": {
        "func": git_log,
        "description": "Получить историю коммитов репозитория.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}, "repo_path": {"type": "string"}}, "required": []}
    },
    "git_diff": {
        "func": git_diff,
        "description": "Получить diff изменений.",
        "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}, "repo_path": {"type": "string"}}, "required": []}
    },
    "git_branches": {
        "func": git_branches,
        "description": "Получить список веток репозитория.",
        "parameters": {"type": "object", "properties": {"repo_path": {"type": "string"}}, "required": []}
    },
    "git_add": {
        "func": git_add,
        "description": "Индексировать изменения (git add).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "repo_path": {"type": "string"}}, "required": []}
    },
    "git_commit": {
        "func": git_commit,
        "description": "Зафиксировать коммит.",
        "parameters": {"type": "object", "properties": {"message": {"type": "string"}, "repo_path": {"type": "string"}}, "required": ["message"]}
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
                "repo_path": {"type": "string"}
            },
            "required": ["action"]
        }
    },
    "git_push": {
        "func": git_push,
        "description": "Отправка изменений в remote (включая bare-репозитории).",
        "parameters": {
            "type": "object",
            "properties": {
                "remote": {"type": "string"},
                "branch": {"type": "string"},
                "set_upstream": {"type": "boolean"},
                "mirror": {"type": "boolean"},
                "repo_path": {"type": "string"}
            }
        }
    },
    "git_fetch": {
        "func": git_fetch,
        "description": "Получение изменений из remote.",
        "parameters": {
            "type": "object",
            "properties": {
                "remote": {"type": "string"},
                "prune": {"type": "boolean"},
                "repo_path": {"type": "string"}
            }
        }
    }
}

def execute_tool(tool_name: str, arguments: dict, cfg: dict = None) -> dict:
    if tool_name not in TOOL_REGISTRY:
        return {"error": f"Неизвестный инструмент: {tool_name}"}
    tool = TOOL_REGISTRY[tool_name]
    try:
        return tool["func"](arguments or {}, cfg or {})
    except Exception as e:
        logger.exception(f"Ошибка выполнения инструмента {tool_name}: {e}")
        return {"error": f"Внутренняя ошибка: {str(e)}"}

GIT_TOOLS = TOOL_REGISTRY
