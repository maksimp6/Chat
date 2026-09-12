"""Локальные MCP-инструменты для работы с Git в Termux."""
import subprocess
import os
import logging

logger = logging.getLogger("git_mcp")

def _get_repo_path(cfg: dict = None) -> str:
    cfg = cfg or {}
    path = cfg.get("repo_path")
    if path and os.path.isdir(path):
        return path
    if os.path.isdir("/sdcard/alice_pro"):
        return "/sdcard/alice_pro"
    return os.getcwd()

def _run_git_command(args: list, cfg: dict = None) -> dict:
    cfg = cfg or {}
    repo_path = _get_repo_path(cfg)
    timeout = cfg.get("timeout", 15)
    allowed_commands = cfg.get("allowed_commands", ["status", "log", "diff", "branch", "show"])

    if not os.path.isdir(repo_path):
        return {"error": f"Директория не найдена: {repo_path}"}

    if args and args[0] not in allowed_commands:
        return {"error": f"Команда не разрешена: {args[0]}"}

    cmd = ["git", "-C", repo_path] + args

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode == 0:
            return {"success": True, "output": result.stdout.strip() or "(пустой вывод / изменений нет)"}
        else:
            return {"success": False, "error": result.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Превышено время ожидания ({timeout} сек)"}
    except Exception as e:
        logger.exception(f"Ошибка выполнения git: {e}")
        return {"success": False, "error": str(e)}

def git_status(args: dict, cfg: dict) -> dict:
    return _run_git_command(["status", "--short", "--branch"], cfg)

def git_log(args: dict, cfg: dict) -> dict:
    limit = args.get("limit") or cfg.get("default_log_limit", 5)
    return _run_git_command(["log", f"-n{limit}", "--oneline", "--no-decorate"], cfg)

def git_diff(args: dict, cfg: dict) -> dict:
    cmd_args = ["diff", "HEAD"]
    if args.get("file_path"):
        cmd_args.append("--")
        cmd_args.append(args["file_path"])
    return _run_git_command(cmd_args, cfg)

def git_branches(args: dict, cfg: dict) -> dict:
    return _run_git_command(["branch", "--list"], cfg)

TOOL_REGISTRY = {
    "git_status": {
        "func": git_status,
        "description": "Получить краткий статус изменений и текущей ветки в Git репозитории.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    "git_log": {
        "func": git_log,
        "description": "Получить историю последних коммитов репозитория.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []}
    },
    "git_diff": {
        "func": git_diff,
        "description": "Получить diff изменений в репозитории или конкретном файле.",
        "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": []}
    },
    "git_branches": {
        "func": git_branches,
        "description": "Получить список веток репозитория.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    }
}

def execute_tool(tool_name: str, arguments: dict, cfg: dict = None) -> dict:
    if tool_name not in TOOL_REGISTRY:
        return {"error": f"Неизвестный инструмент: {tool_name}"}
    tool = TOOL_REGISTRY[tool_name]
    try:
        return tool["func"](arguments, cfg or {})
    except Exception as e:
        logger.exception(f"Ошибка выполнения инструмента {tool_name}: {e}")
        return {"error": f"Внутренняя ошибка: {str(e)}"}
