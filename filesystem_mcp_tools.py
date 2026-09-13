"""Инструменты стандартной библиотеки Python и файловой системы для Alice Pro.
Включает:
- C-style позиционированное чтение файлов (fseek + fread) для мгновенного чтения хвостов логов
- Безопасную атомарную запись файлов с проверкой синтаксиса (py_compile) и автобэкапом
- Анализ структуры кода через AST (ast.parse): классы, методы, функции, импорты
- Инспекцию локальных баз данных SQLite (sqlite3)
- Вычисление контрольных сумм (hashlib: sha256, md5, sha1)
- Работу с архивами (zipfile: сжатие и безопасная распаковка с защитой от Zip Slip)
- Сетевые HTTP-запросы без внешних зависимостей (urllib.request)
- Поиск файлов (glob) и содержимого (grep / regex)
- Управление файлами и папками (os, shutil): copy, move, delete, mkdir
- Запуск bash-команд (subprocess) и сбор сведений о системе (platform, shutil)
"""
import os
import re
import ast
import sys
import glob
import shutil
import difflib
import hashlib
import zipfile
import sqlite3
import logging
import tempfile
import platform
import subprocess
import urllib.request
import urllib.error

logger = logging.getLogger("filesystem_mcp")

BASE_DIR = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(BASE_DIR, ".safe_backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

def _get_abs_path(path: str) -> str:
    """Нормализует путь и блокирует атаки Path Traversal за пределы проекта."""
    if not path or path.strip() in (".", "./"):
        return BASE_DIR
    clean = os.path.normpath(path).lstrip("/")
    target = os.path.realpath(os.path.join(BASE_DIR, clean))
    try:
        common = os.path.commonpath([BASE_DIR, target])
        if common != BASE_DIR:
            raise PermissionError("Доступ запрещен: выход за пределы директории проекта")
    except ValueError:
        raise PermissionError("Доступ запрещен: недопустимый путь к файлу")
    return target

def read_file(args: dict) -> dict:
    """Низкоуровневое позиционированное чтение файлов (аналог C fopen + fseek + fread)."""
    path = args.get("path") or args.get("file_path") or args.get("filename")
    if not path:
        return {"error": "Параметр 'path' обязателен"}
    try:
        abs_path = _get_abs_path(path)
        if not os.path.isfile(abs_path):
            return {"error": f"Файл '{path}' не найден"}

        file_size = os.path.getsize(abs_path)
        raw_whence = args.get("whence", 0)
        if isinstance(raw_whence, str):
            w_norm = raw_whence.lower().strip()
            if w_norm in ("end", "seek_end", "2"):
                whence = os.SEEK_END
            elif w_norm in ("cur", "current", "seek_cur", "1"):
                whence = os.SEEK_CUR
            else:
                whence = os.SEEK_SET
        else:
            try:
                whence = int(raw_whence)
            except (ValueError, TypeError):
                whence = os.SEEK_SET

        try:
            offset = int(args.get("offset", 0))
        except (ValueError, TypeError):
            offset = 0

        max_chunk = 64 * 1024
        raw_length = args.get("length") or args.get("size") or args.get("count")
        try:
            length = int(raw_length) if raw_length is not None else None
        except (ValueError, TypeError):
            length = None

        with open(abs_path, "rb") as f:
            if whence == os.SEEK_END:
                read_bytes = length if (length is not None and length > 0) else min(file_size, max_chunk)
                seek_delta = -abs(offset) if offset != 0 else -read_bytes
                f.seek(max(-file_size, seek_delta), os.SEEK_END)
            elif whence == os.SEEK_SET:
                f.seek(max(0, min(offset, file_size)), os.SEEK_SET)
            else:
                f.seek(offset, os.SEEK_CUR)

            actual_offset = f.tell()
            read_size = length if (length is not None and length > 0) else max_chunk
            chunk_data = f.read(read_size)
            has_more = f.tell() < file_size
            content = chunk_data.decode("utf-8", errors="replace")

            return {
                "success": True,
                "path": path,
                "file_size": file_size,
                "offset": actual_offset,
                "bytes_read": len(chunk_data),
                "has_more": has_more,
                "content": content,
                "lines_count": len(content.splitlines())
            }
    except Exception as e:
        logger.exception(f"[FS] Ошибка чтения файла {path}: {e}")
        return {"error": f"Ошибка чтения: {str(e)}"}

def write_file(args: dict) -> dict:
    """Безопасная запись файла с валидацией синтаксиса Python и автобэкапом."""
    path = args.get("path") or args.get("file_path") or args.get("filename")
    content = args.get("content") if "content" in args else (args.get("text") or args.get("code"))
    if not path:
        return {"error": "Параметр 'path' обязателен"}
    if content is None:
        return {"error": "Параметр 'content' обязателен"}

    tmp_path = None
    try:
        abs_path = _get_abs_path(path)
        target_dir = os.path.dirname(abs_path)
        os.makedirs(target_dir, exist_ok=True)

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target_dir, delete=False, prefix=".tmp_", suffix=".tmp") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        if abs_path.endswith(".py"):
            verify = subprocess.run(
                ["python3", "-m", "py_compile", tmp_path],
                capture_output=True,
                text=True
            )
            if verify.returncode != 0:
                if os.path.exists(tmp_path):
                    try: os.remove(tmp_path)
                    except OSError: pass
                err_msg = verify.stderr.strip() or verify.stdout.strip()
                logger.error(f"[FS] Ошибка синтаксиса при записи в {path}: {err_msg}")
                return {
                    "success": False,
                    "error": f"Файл не записан: синтаксическая ошибка Python:\n{err_msg}",
                    "reverted": True
                }

        old_content = ""
        if os.path.isfile(abs_path):
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    old_content = f.read()
                backup_file = os.path.join(BACKUP_DIR, f"{os.path.basename(abs_path)}.bak")
                shutil.copy2(abs_path, backup_file)
            except Exception as be:
                logger.warning(f"[FS] Не удалось создать бэкап {path}: {be}")

        try:
            os.replace(tmp_path, abs_path)
        except OSError as err:
            if err.errno == 18:
                shutil.move(tmp_path, abs_path)
            else:
                raise

        diff = list(difflib.unified_diff(
            old_content.splitlines(),
            content.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm=""
        ))

        return {
            "success": True,
            "path": path,
            "message": f"Файл '{path}' успешно проверен и сохранен.",
            "diff": "\n".join(diff[:60]) if diff else "(файл создан заново)"
        }
    except Exception as e:
        if tmp_path and os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except OSError: pass
        logger.exception(f"[FS] Ошибка записи файла {path}: {e}")
        return {"error": f"Ошибка записи: {str(e)}"}

def apply_patch(args: dict, cfg: dict = None) -> dict:
    """Точечно заменить участок текста в файле на новый."""
    path = args.get("file_path") or args.get("path")
    search_text = args.get("search_text")
    replace_text = args.get("replace_text") or args.get("patch") or args.get("diff")
    if not path or not search_text:
        return {"success": False, "error": "Параметры 'path' и 'search_text' обязательны"}
    try:
        abs_path = _get_abs_path(path)
        if not os.path.isfile(abs_path):
            return {"success": False, "error": f"Файл не найден: {path}"}
        
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
            
        if search_text not in content:
            return {"success": False, "error": "Искомый текст (search_text) не найден в файле."}
            
        content = content.replace(search_text, replace_text or "", 1)
        
        # Переиспользуем write_file для сохранения с проверкой синтаксиса
        return write_file({"path": path, "content": content})
    except Exception as e:
        return {"success": False, "error": str(e)}

def python_ast_outline(args: dict) -> dict:
    """Анализ структуры Python-файла через модуль ast без его выполнения."""
    path = args.get("path")
    if not path:
        return {"error": "Параметр 'path' обязателен"}
    try:
        abs_path = _get_abs_path(path)
        if not os.path.isfile(abs_path):
            return {"error": f"Файл '{path}' не найден"}

        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()

        try:
            tree = ast.parse(code, filename=path)
        except SyntaxError as se:
            return {"success": False, "error": f"Синтаксическая ошибка на строке {se.lineno}: {se.msg}"}

        classes = []
        functions = []
        imports = []

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                methods = []
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        args_list = [a.arg for a in sub.args.args]
                        methods.append({"name": sub.name, "line": sub.lineno, "args": args_list})
                classes.append({
                    "name": node.name,
                    "line": node.lineno,
                    "docstring": ast.get_docstring(node) or "",
                    "methods": methods
                })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args_list = [a.arg for a in node.args.args]
                functions.append({
                    "name": node.name,
                    "line": node.lineno,
                    "args": args_list,
                    "docstring": ast.get_docstring(node) or ""
                })
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    imports.append(f"{mod}.{alias.name}")

        return {
            "success": True,
            "path": path,
            "total_lines": len(code.splitlines()),
            "docstring": ast.get_docstring(tree) or "",
            "imports": sorted(list(set(imports))),
            "classes": classes,
            "functions": functions
        }
    except Exception as e:
        return {"error": f"Ошибка AST-анализа: {str(e)}"}

def sqlite_query(args: dict) -> dict:
    """Выполнение безопасного запроса к локальной базе данных SQLite."""
    db_name = args.get("db_path", "alice_pro.db")
    query = args.get("query")
    if not query:
        return {"error": "Параметр 'query' обязателен"}

    limit = int(args.get("limit", 50))
    try:
        abs_db = _get_abs_path(db_name)
        if not os.path.isfile(abs_db):
            return {"error": f"База данных '{db_name}' не найдена"}

        conn = sqlite3.connect(abs_db, timeout=5)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(query)
        if cur.description:
            columns = [col[0] for col in cur.description]
            rows = cur.fetchmany(limit)
            results = [dict(zip(columns, row)) for row in rows]
            conn.close()
            return {
                "success": True,
                "db_path": db_name,
                "columns": columns,
                "count": len(results),
                "rows": results
            }
        else:
            conn.commit()
            changes = conn.total_changes
            conn.close()
            return {
                "success": True,
                "db_path": db_name,
                "message": f"Запрос выполнен. Изменено строк: {changes}"
            }
    except Exception as e:
        return {"error": f"Ошибка выполнения SQLite: {str(e)}"}

def calculate_hash(args: dict) -> dict:
    """Вычисление хеша файла (sha256, md5, sha1) для проверки целостности."""
    path = args.get("path")
    if not path:
        return {"error": "Параметр 'path' обязателен"}
    algo_name = args.get("algorithm", "sha256").lower()
    if algo_name not in ("sha256", "md5", "sha1"):
        return {"error": "Поддерживаются алгоритмы: sha256, md5, sha1"}

    try:
        abs_path = _get_abs_path(path)
        if not os.path.isfile(abs_path):
            return {"error": f"Файл '{path}' не найден"}

        hasher = getattr(hashlib, algo_name)()
        with open(abs_path, "rb") as f:
            while chunk := f.read(64 * 1024):
                hasher.update(chunk)

        return {
            "success": True,
            "path": path,
            "algorithm": algo_name,
            "digest": hasher.hexdigest(),
            "size_bytes": os.path.getsize(abs_path)
        }
    except Exception as e:
        return {"error": f"Ошибка вычисления хеша: {str(e)}"}

def zip_compress(args: dict) -> dict:
    """Создание ZIP-архива файлов или каталога."""
    source_path = args.get("source_path")
    out_archive = args.get("archive_path")
    if not source_path or not out_archive:
        return {"error": "Параметры 'source_path' и 'archive_path' обязательны"}

    try:
        abs_src = _get_abs_path(source_path)
        abs_out = _get_abs_path(out_archive)
        if not os.path.exists(abs_src):
            return {"error": f"Исходный путь '{source_path}' не существует"}

        os.makedirs(os.path.dirname(abs_out), exist_ok=True)
        count = 0
        with zipfile.ZipFile(abs_out, "w", zipfile.ZIP_DEFLATED) as zf:
            if os.path.isfile(abs_src):
                zf.write(abs_src, arcname=os.path.basename(abs_src))
                count = 1
            else:
                for root, _, files in os.walk(abs_src):
                    for file in files:
                        full_f = os.path.join(root, file)
                        rel_f = os.path.relpath(full_f, abs_src)
                        zf.write(full_f, arcname=rel_f)
                        count += 1

        return {
            "success": True,
            "archive_path": out_archive,
            "files_archived": count,
            "archive_size_bytes": os.path.getsize(abs_out)
        }
    except Exception as e:
        return {"error": f"Ошибка создания архива: {str(e)}"}

def zip_extract(args: dict) -> dict:
    """Безопасная распаковка ZIP-архива с защитой от Zip Slip."""
    archive_path = args.get("archive_path")
    target_dir = args.get("target_dir", ".")
    if not archive_path:
        return {"error": "Параметр 'archive_path' обязателен"}

    try:
        abs_zip = _get_abs_path(archive_path)
        abs_dst = _get_abs_path(target_dir)
        if not os.path.isfile(abs_zip):
            return {"error": f"Архив '{archive_path}' не найден"}

        os.makedirs(abs_dst, exist_ok=True)
        extracted = []
        with zipfile.ZipFile(abs_zip, "r") as zf:
            for member in zf.infolist():
                target_path = os.path.realpath(os.path.join(abs_dst, member.filename))
                if os.path.commonpath([abs_dst, target_path]) != abs_dst:
                    raise PermissionError(f"Опасный путь в архиве: {member.filename}")
                zf.extract(member, abs_dst)
                extracted.append(member.filename)

        return {
            "success": True,
            "archive_path": archive_path,
            "target_dir": target_dir,
            "extracted_count": len(extracted),
            "files": extracted[:50]
        }
    except Exception as e:
        return {"error": f"Ошибка распаковки архива: {str(e)}"}

def http_fetch(args: dict) -> dict:
    """Простой HTTP GET-запрос через стандартный urllib."""
    url = args.get("url")
    if not url:
        return {"error": "Параметр 'url' обязателен"}
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"error": "URL должен начинаться с http:// или https://"}

    timeout = int(args.get("timeout", 10))
    max_bytes = 256 * 1024

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "AlicePro-StdLib/1.0", "Accept": "*/*"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes)
            status_code = resp.getcode()
            headers = dict(resp.info())

            text = raw.decode("utf-8", errors="replace")
            return {
                "success": True,
                "url": url,
                "status": status_code,
                "bytes_received": len(raw),
                "headers": {k: headers[k] for k in list(headers.keys())[:10]},
                "body": text[:2000]
            }
    except urllib.error.HTTPError as he:
        return {"success": False, "status": he.code, "error": f"HTTP Error {he.code}: {he.reason}"}
    except Exception as e:
        return {"success": False, "error": f"Ошибка сетевого запроса: {str(e)}"}

def make_directory(args: dict) -> dict:
    """Создание директории проекта (mkdir -p)."""
    path = args.get("path")
    if not path:
        return {"error": "Параметр 'path' обязателен"}
    try:
        abs_path = _get_abs_path(path)
        os.makedirs(abs_path, exist_ok=True)
        return {"success": True, "path": path, "message": f"Директория '{path}' создана."}
    except Exception as e:
        return {"error": f"Ошибка создания папки: {str(e)}"}

def copy_or_move_file(args: dict) -> dict:
    """Копирование или перемещение/переименование файлов и папок."""
    src = args.get("src")
    dst = args.get("dst")
    action = args.get("action", "copy").lower()
    if not src or not dst:
        return {"error": "Параметры 'src' и 'dst' обязательны"}

    try:
        abs_src = _get_abs_path(src)
        abs_dst = _get_abs_path(dst)
        if not os.path.exists(abs_src):
            return {"error": f"Исходный файл '{src}' не существует"}

        os.makedirs(os.path.dirname(abs_dst), exist_ok=True)
        if action == "move":
            shutil.move(abs_src, abs_dst)
            msg = f"Успешно перемещено '{src}' -> '{dst}'"
        else:
            if os.path.isdir(abs_src):
                shutil.copytree(abs_src, abs_dst, dirs_exist_ok=True)
            else:
                shutil.copy2(abs_src, abs_dst)
            msg = f"Успешно скопировано '{src}' -> '{dst}'"

        return {"success": True, "action": action, "message": msg}
    except Exception as e:
        return {"error": f"Ошибка {action}: {str(e)}"}

def delete_path(args: dict) -> dict:
    """Безопасное удаление файла или папки с защитой корня проекта и .git."""
    path = args.get("path")
    if not path:
        return {"error": "Параметр 'path' обязателен"}

    try:
        abs_path = _get_abs_path(path)
        if abs_path == BASE_DIR:
            return {"error": "Запрещено удалять корневую директорию проекта"}
        if ".git" in abs_path.split(os.sep):
            return {"error": "Запрещено удалять файлы репозитория Git"}
        if not os.path.exists(abs_path):
            return {"error": f"Путь '{path}' не найден"}

        if os.path.isdir(abs_path):
            shutil.rmtree(abs_path)
            kind = "папка"
        else:
            os.remove(abs_path)
            kind = "файл"

        return {"success": True, "path": path, "message": f"{kind.capitalize()} '{path}' удален."}
    except Exception as e:
        return {"error": f"Ошибка удаления: {str(e)}"}

def list_directory(args: dict) -> dict:
    """Получение списка файлов и папок в директории проекта."""
    path = args.get("path") or "."
    try:
        abs_path = _get_abs_path(path)
        if not os.path.isdir(abs_path):
            return {"error": f"Директория '{path}' не найдена"}

        items = []
        for entry in os.scandir(abs_path):
            if entry.name.startswith((".", "__pycache__")):
                continue
            items.append({
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "size": entry.stat().st_size if not entry.is_dir() else None
            })
        items.sort(key=lambda x: (not x["is_dir"], x["name"]))
        return {"success": True, "path": path, "items": items}
    except Exception as e:
        return {"error": f"Ошибка сканирования директории: {str(e)}"}

def glob_search(args: dict) -> dict:
    """Поиск файлов по шаблону маски (glob)."""
    pattern = args.get("pattern") or "*.*"
    try:
        full_pattern = os.path.join(BASE_DIR, pattern.lstrip("/"))
        matches = glob.glob(full_pattern, recursive=True)
        results = []
        for p in sorted(matches)[:100]:
            rel = os.path.relpath(p, BASE_DIR)
            if not rel.startswith((".", "__pycache__")):
                results.append({
                    "path": rel,
                    "is_dir": os.path.isdir(p),
                    "size": os.path.getsize(p) if os.path.isfile(p) else 0
                })
        return {"success": True, "pattern": pattern, "count": len(results), "matches": results}
    except Exception as e:
        return {"error": f"Ошибка поиска по маске: {str(e)}"}

def grep_search(args: dict) -> dict:
    """Полнотекстовый и регулярный поиск строк по файлам проекта."""
    query = args.get("query")
    if not query:
        return {"error": "Параметр 'query' обязателен"}
    file_pattern = args.get("file_pattern", "*.py")
    max_matches = int(args.get("max_matches", 50))
    case_sensitive = args.get("case_sensitive", False)

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(query, flags)
    except re.error as err:
        return {"error": f"Невалидное регулярное выражение: {err}"}

    results = []
    try:
        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if not d.startswith((".", "__pycache__", "node_modules"))]
            for file in files:
                if not glob.fnmatch.fnmatch(file, file_pattern):
                    continue
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, BASE_DIR)
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, start=1):
                            if regex.search(line):
                                results.append({
                                    "file": rel_path,
                                    "line": line_num,
                                    "text": line.strip()[:200]
                                })
                                if len(results) >= max_matches:
                                    return {"success": True, "query": query, "count": len(results), "truncated": True, "matches": results}
                except Exception:
                    continue
        return {"success": True, "query": query, "count": len(results), "truncated": False, "matches": results}
    except Exception as e:
        return {"error": f"Ошибка grep-поиска: {str(e)}"}

def file_stat(args: dict) -> dict:
    """Получение детальных метаданных файла."""
    path = args.get("path")
    if not path:
        return {"error": "Параметр 'path' обязателен"}
    try:
        abs_path = _get_abs_path(path)
        if not os.path.exists(abs_path):
            return {"error": f"Путь '{path}' не существует"}

        st = os.stat(abs_path)
        return {
            "success": True,
            "path": path,
            "is_file": os.path.isfile(abs_path),
            "is_dir": os.path.isdir(abs_path),
            "size_bytes": st.st_size,
            "created_time": int(st.st_ctime),
            "modified_time": int(st.st_mtime),
            "permissions_octal": oct(st.st_mode)[-3:]
        }
    except Exception as e:
        return {"error": f"Ошибка чтения метаданных: {str(e)}"}

def run_command(args: dict) -> dict:
    """Безопасное выполнение терминальной команды bash в рабочей директории."""
    command = args.get("command") or args.get("cmd")
    if not command:
        return {"error": "Параметр 'command' обязателен"}

    timeout = int(args.get("timeout", 30))
    try:
        res = subprocess.run(
            command,
            shell=True,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return {
            "success": res.returncode == 0,
            "command": command,
            "returncode": res.returncode,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip()
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Превышено время ожидания команды ({timeout} сек)"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_system_info(args: dict) -> dict:
    """Получение информации о дисковом пространстве, ОС и версии Python."""
    try:
        total, used, free = shutil.disk_usage(BASE_DIR)
        return {
            "success": True,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python_version": sys.version.split()[0],
            "project_dir": BASE_DIR,
            "disk_total_gb": round(total / (1024 ** 3), 2),
            "disk_used_gb": round(used / (1024 ** 3), 2),
            "disk_free_gb": round(free / (1024 ** 3), 2)
        }
    except Exception as e:
        return {"error": f"Ошибка сбора системной информации: {str(e)}"}

TOOL_REGISTRY = {
    "apply_patch": {
        "func": apply_patch,
        "description": "Apply patch/changes to file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "patch": {"type": "string"}}, "required": ["path", "patch"]}
    },
    "read_file": {
        "func": read_file,
        "description": "Позиционированное чтение файла в стиле Си (fseek + fread). Позволяет читать хвосты логов (whence='end') и фрагменты произвольной длины без перегрузки памяти.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к файлу"},
                "offset": {"type": "integer", "description": "Смещение байт (при whence='end' отступает назад от конца)"},
                "length": {"type": "integer", "description": "Размер буфера чтения в байтах (по умолчанию 64KB)"},
                "whence": {"type": "string", "description": "Точка отсчета: 0/'start' (SEEK_SET), 1/'cur' (SEEK_CUR), 2/'end' (SEEK_END для tail)"}
            },
            "required": ["path"]
        },
        "requires_approval": False
    },
    "write_file": {
        "func": write_file,
        "description": "Безопасная запись файла с валидацией синтаксиса Python перед сохранением.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к файлу"},
                "content": {"type": "string", "description": "Новое содержимое файла"}
            },
            "required": ["path", "content"]
        },
        "requires_approval": False
    },
    "python_ast_outline": {
        "func": python_ast_outline,
        "description": "Быстрый анализ структуры Python-файла: список классов, функций, методов с аргументами и импортов без выполнения кода.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к .py файлу для анализа структуры"}
            },
            "required": ["path"]
        },
        "requires_approval": False
    },
    "sqlite_query": {
        "func": sqlite_query,
        "description": "Выполнение SQL-запроса к локальной базе данных SQLite (alice_pro.db или mcp_servers.db).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "SQL запрос для выполнения"},
                "db_path": {"type": "string", "description": "Имя файла БД (по умолчанию alice_pro.db)"},
                "limit": {"type": "integer", "description": "Лимит возвращаемых строк (по умолчанию 50)"}
            },
            "required": ["query"]
        },
        "requires_approval": False
    },
    "calculate_hash": {
        "func": calculate_hash,
        "description": "Вычислить контрольную сумму файла (sha256, md5, sha1) для проверки целостности.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к файлу"},
                "algorithm": {"type": "string", "enum": ["sha256", "md5", "sha1"], "description": "Алгоритм хеширования"}
            },
            "required": ["path"]
        },
        "requires_approval": False
    },
    "zip_compress": {
        "func": zip_compress,
        "description": "Сжать файл или каталог в ZIP-архив.",
        "parameters": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "Исходный файл или папка"},
                "archive_path": {"type": "string", "description": "Путь к создаваемому .zip файлу"}
            },
            "required": ["source_path", "archive_path"]
        },
        "requires_approval": False
    },
    "zip_extract": {
        "func": zip_extract,
        "description": "Распаковать ZIP-архив в указанную директорию.",
        "parameters": {
            "type": "object",
            "properties": {
                "archive_path": {"type": "string", "description": "Путь к архиву .zip"},
                "target_dir": {"type": "string", "description": "Директория назначения (по умолчанию '.')"}
            },
            "required": ["archive_path"]
        },
        "requires_approval": False
    },
    "http_fetch": {
        "func": http_fetch,
        "description": "Выполнить базовый HTTP GET-запрос по URL и получить код ответа и текст без внешних зависимостей.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL адрес (http/https)"},
                "timeout": {"type": "integer", "description": "Таймаут в секундах (по умолчанию 10)"}
            },
            "required": ["url"]
        },
        "requires_approval": False
    },
    "make_directory": {
        "func": make_directory,
        "description": "Создать новую директорию в проекте (включая промежуточные папки).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к создаваемой папке"}
            },
            "required": ["path"]
        },
        "requires_approval": False
    },
    "copy_or_move_file": {
        "func": copy_or_move_file,
        "description": "Скопировать или переместить/переименовать файл или папку.",
        "parameters": {
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "Исходный путь"},
                "dst": {"type": "string", "description": "Целевой путь"},
                "action": {"type": "string", "enum": ["copy", "move"], "description": "Действие: 'copy' или 'move'"}
            },
            "required": ["src", "dst"]
        },
        "requires_approval": False
    },
    "delete_path": {
        "func": delete_path,
        "description": "Удалить файл или папку в проекте.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к удаляемому файлу или директории"}
            },
            "required": ["path"]
        },
        "requires_approval": False
    },
    "list_directory": {
        "func": list_directory,
        "description": "Получение списка файлов и папок в директории проекта.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Путь к директории ('.' по умолчанию)"}},
            "required": []
        },
        "requires_approval": False
    },
    "glob_search": {
        "func": glob_search,
        "description": "Поиск файлов по шаблону маски (glob), например '*.py' или 'logs/*.txt'.",
        "parameters": {
            "type": "object",
            "properties": {"pattern": {"type": "string", "description": "Шаблон поиска (например '*.py')"}},
            "required": ["pattern"]
        },
        "requires_approval": False
    },
    "grep_search": {
        "func": grep_search,
        "description": "Поиск текста или регулярного выражения во всех файлах проекта с номерами строк.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Текст или regex для поиска"},
                "file_pattern": {"type": "string", "description": "Маска файлов (по умолчанию '*.py')"},
                "max_matches": {"type": "integer", "description": "Максимум совпадений (по умолчанию 50)"}
            },
            "required": ["query"]
        },
        "requires_approval": False
    },
    "file_stat": {
        "func": file_stat,
        "description": "Получить метаданные файла (размер в байтах, дата создания, дата изменения, права доступа).",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Путь к файлу"}},
            "required": ["path"]
        },
        "requires_approval": False
    },
    "run_command": {
        "func": run_command,
        "description": "Выполнить bash-команду в рабочей директории проекта Termux и получить stdout/stderr.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Команда для терминала"},
                "timeout": {"type": "integer", "description": "Таймаут выполнения в секундах (по умолчанию 30)"}
            },
            "required": ["command"]
        },
        "requires_approval": False
    },
    "get_system_info": {
        "func": get_system_info,
        "description": "Получить данные о дисковом пространстве, платформе Android/Termux и версии Python.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_approval": False
    }
}

FILESYSTEM_TOOLS = TOOL_REGISTRY

def execute_fs_tool(tool_name: str, arguments: dict) -> dict:
    """Точка входа для выполнения зарегистрированных инструментов файловой системы."""
    if tool_name not in TOOL_REGISTRY:
        return {"error": f"Неизвестный системный инструмент: {tool_name}"}
    return TOOL_REGISTRY[tool_name]["func"](arguments)
