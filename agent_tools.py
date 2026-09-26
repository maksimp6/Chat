import os
import subprocess
import json
import re

TOOLS_SCHEMA = [
    # --- GIT TOOLS (с поддержкой normal и bare репозиториев) ---
    {
        "name": "git_init",
        "description": "Инициализировать git репозиторий (обычный или bare).",
        "parameters": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Путь к каталогу"},
                "bare": {
                    "type": "boolean",
                    "description": "Флаг True для создания bare репозитория (--bare)",
                },
                "initial_branch": {
                    "type": "string",
                    "description": "Начальная ветка (по умолчанию main)",
                },
            },
            "required": ["repo_path"],
        },
    },
    {
        "name": "git_clone",
        "description": "Клонировать репозиторий, включая поддержку bare и mirror.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL репозитория (https, ssh или локальный путь)",
                },
                "target_path": {"type": "string", "description": "Каталог назначения"},
                "bare": {"type": "boolean", "description": "Клонировать как bare (--bare)"},
                "mirror": {"type": "boolean", "description": "Клонировать как mirror (--mirror)"},
            },
            "required": ["url", "target_path"],
        },
    },
    {
        "name": "git_status",
        "description": "Показать статус git репозитория. Корректно определяет bare-режим.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Путь к git репозиторию"},
                "work_tree": {
                    "type": "string",
                    "description": "Рабочее дерево (обязательно для bare репозитория при проверке статуса файлов)",
                },
            },
        },
    },
    {
        "name": "git_commit",
        "description": "Создать коммит. Если репозиторий bare, требуется work_tree.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Сообщение коммита"},
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Список файлов для добавления",
                },
                "repo_path": {"type": "string", "description": "Путь к git репозиторию"},
                "work_tree": {
                    "type": "string",
                    "description": "Путь к рабочей директории, если репозиторий bare",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "git_remotes",
        "description": "Управление remote репозиториями (origin) как для обычных, так и для bare репозиториев.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "add", "set_url", "remove", "show"],
                    "description": "Действие",
                },
                "name": {"type": "string", "description": "Имя remote (по умолчанию 'origin')"},
                "url": {"type": "string", "description": "URL удаленного репозитория"},
                "repo_path": {"type": "string", "description": "Путь к git репозиторию"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "git_push",
        "description": "Отправка изменений в remote (включая mirror и tags).",
        "parameters": {
            "type": "object",
            "properties": {
                "remote": {"type": "string", "description": "Имя remote (например 'origin')"},
                "branch": {"type": "string", "description": "Ветка или refspec"},
                "mirror": {
                    "type": "boolean",
                    "description": "Флаг --mirror для полной репликации bare репозитория",
                },
                "set_upstream": {"type": "boolean", "description": "Флаг -u"},
                "repo_path": {"type": "string", "description": "Путь к git репозиторию"},
            },
        },
    },
    {
        "name": "git_fetch",
        "description": "Получить обновления из remote без слияния в рабочее дерево (идеально для bare).",
        "parameters": {
            "type": "object",
            "properties": {
                "remote": {"type": "string", "description": "Имя remote (по умолчанию 'origin')"},
                "prune": {
                    "type": "boolean",
                    "description": "Флаг --prune для очистки устаревших веток",
                },
                "repo_path": {"type": "string", "description": "Путь к git репозиторию"},
            },
        },
    },
    {
        "name": "git_log",
        "description": "Показать историю последних коммитов (работает и в bare репозиториях).",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Количество коммитов (по умолчанию 5)"},
                "repo_path": {"type": "string", "description": "Путь к git репозиторию"},
            },
        },
    },
    # --- СИСТЕМНЫЕ ИНСТРУМЕНТЫ ---
    {
        "name": "file_replace_text",
        "description": "Безопасная замена подстроки в файле с нормализацией строк.",
        "parameters": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Путь к файлу"},
                "old_text": {"type": "string", "description": "Исходный фрагмент текста"},
                "new_text": {"type": "string", "description": "Новый фрагмент текста"},
            },
            "required": ["filepath", "old_text", "new_text"],
        },
    },
    {
        "name": "shell_execute",
        "description": "Выполнить shell-команду в системе.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "Команда для терминала"}},
            "required": ["command"],
        },
    },
]


class ToolExecutor:
    @staticmethod
    def _run_git(args: list, repo_path: str = ".", work_tree: str = None):
        cmd = ["git"]

        # Если явно передан work_tree или bare git dir
        if repo_path and repo_path != ".":
            cmd.extend(
                ["--git-dir", repo_path]
                if os.path.exists(os.path.join(repo_path, "HEAD"))
                and not os.path.exists(os.path.join(repo_path, ".git"))
                else ["-C", repo_path]
            )

        if work_tree:
            cmd.extend(["--work-tree", work_tree])

        cmd.extend(args)

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return {
                "success": res.returncode == 0,
                "stdout": res.stdout.strip(),
                "stderr": res.stderr.strip(),
                "exit_code": res.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Превышено время ожидания команды git (30с)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def _is_bare(cls, repo_path: str):
        check = cls._run_git(["rev-parse", "--is-bare-repository"], repo_path=repo_path)
        return check.get("stdout") == "true"

    @classmethod
    def git_init(cls, repo_path: str, bare: bool = False, initial_branch: str = "main"):
        os.makedirs(repo_path, exist_ok=True)
        args = ["init", f"--initial-branch={initial_branch}"]
        if bare:
            args.append("--bare")
        return cls._run_git(args, repo_path=repo_path)

    @classmethod
    def git_clone(cls, url: str, target_path: str, bare: bool = False, mirror: bool = False):
        args = ["clone"]
        if mirror:
            args.append("--mirror")
        elif bare:
            args.append("--bare")
        args.extend([url, target_path])
        return cls._run_git(args)

    @classmethod
    def git_status(cls, repo_path: str = ".", work_tree: str = None):
        is_bare = cls._is_bare(repo_path)
        if is_bare and not work_tree:
            # Для bare-репозитория без work_tree статус рабочей зоны не применим
            branch_info = cls._run_git(["branch", "-a"], repo_path=repo_path)
            head_info = cls._run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path=repo_path)
            return {
                "success": True,
                "is_bare": True,
                "head_branch": head_info.get("stdout"),
                "branches": branch_info.get("stdout").split("\n")
                if branch_info.get("stdout")
                else [],
                "note": "Это bare-репозиторий без рабочей директории. Проверка рабочих файлов не требуется.",
            }

        res = cls._run_git(["status", "-sb"], repo_path=repo_path, work_tree=work_tree)
        res["is_bare"] = is_bare
        return res

    @classmethod
    def git_commit(
        cls, message: str, files: list = None, repo_path: str = ".", work_tree: str = None
    ):
        if cls._is_bare(repo_path) and not work_tree:
            return {
                "success": False,
                "error": "Нельзя сделать git commit в bare-репозиторий без указания параметра 'work_tree'.",
            }

        add_target = files if files else ["."]
        add_res = cls._run_git(["add"] + add_target, repo_path=repo_path, work_tree=work_tree)
        if not add_res["success"]:
            return {
                "success": False,
                "error": "Ошибка при добавлении файлов в индекс",
                "details": add_res,
            }

        return cls._run_git(["commit", "-m", message], repo_path=repo_path, work_tree=work_tree)

    @classmethod
    def git_remotes(cls, action: str, name: str = "origin", url: str = None, repo_path: str = "."):
        if action == "list":
            return cls._run_git(["remote", "-v"], repo_path=repo_path)
        elif action == "add":
            if not url:
                return {
                    "success": False,
                    "error": "Параметр 'url' обязателен для добавления remote",
                }
            return cls._run_git(["remote", "add", name, url], repo_path=repo_path)
        elif action == "set_url":
            if not url:
                return {"success": False, "error": "Параметр 'url' обязателен для смены URL remote"}
            return cls._run_git(["remote", "set-url", name, url], repo_path=repo_path)
        elif action == "remove":
            return cls._run_git(["remote", "remove", name], repo_path=repo_path)
        elif action == "show":
            return cls._run_git(["remote", "show", name], repo_path=repo_path)
        return {"success": False, "error": f"Неизвестное действие: {action}"}

    @classmethod
    def git_push(
        cls,
        remote: str = "origin",
        branch: str = None,
        mirror: bool = False,
        set_upstream: bool = False,
        repo_path: str = ".",
    ):
        args = ["push"]
        if mirror:
            args.append("--mirror")
        if set_upstream:
            args.append("-u")
        args.append(remote)
        if branch and not mirror:
            args.append(branch)
        return cls._run_git(args, repo_path=repo_path)

    @classmethod
    def git_fetch(cls, remote: str = "origin", prune: bool = False, repo_path: str = "."):
        args = ["fetch", remote]
        if prune:
            args.append("--prune")
        return cls._run_git(args, repo_path=repo_path)

    @classmethod
    def git_log(cls, count: int = 5, repo_path: str = "."):
        return cls._run_git(["log", f"-n{count}", "--oneline", "--decorate"], repo_path=repo_path)

    # --- ФАЙЛЫ И СИСТЕМА ---
    @staticmethod
    def file_replace_text(filepath: str, old_text: str, new_text: str):
        if not os.path.exists(filepath):
            return {"success": False, "error": f"Файл {filepath} не найден"}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            norm_content = content.replace("\r\n", "\n")
            norm_old = old_text.replace("\r\n", "\n")

            if norm_old not in norm_content:
                return {"success": False, "error": "Фрагмент old_text не найден в файле"}

            updated = norm_content.replace(norm_old, new_text.replace("\r\n", "\n"), 1)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(updated)
            return {"success": True, "file": filepath}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def shell_execute(command: str):
        try:
            res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=20)
            return {
                "success": res.returncode == 0,
                "stdout": res.stdout.strip(),
                "stderr": res.stderr.strip(),
                "exit_code": res.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Команда превысила лимит времени (20с)"}
        except Exception as e:
            return {"success": False, "error": str(e)}


def dispatch_tool(tool_name: str, args: dict = None):
    args = args or {}
    executor = ToolExecutor()
    if hasattr(executor, tool_name):
        func = getattr(executor, tool_name)
        try:
            return func(**args)
        except TypeError as e:
            return {"success": False, "error": f"Неверные параметры для {tool_name}: {str(e)}"}
    return {"success": False, "error": f"Инструмент {tool_name} не найден"}
