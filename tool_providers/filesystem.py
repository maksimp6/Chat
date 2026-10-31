"""Инструменты стандартной библиотеки Python и файловой системы для Logos.
Включает:
- C-style позиционированное чтение файлов (fseek + fread) для мгновенного чтения хвостов логов
- Безопасную атомарную запись файлов с проверкой синтаксиса (py_compile) и автобэкапом
- Анализ структуры кода через AST (ast.parse): классы, методы, функции, импорты
- Вычисление контрольных сумм (hashlib: sha256, md5, sha1)
- Работу с архивами (zipfile: сжатие и безопасная распаковка с защитой от Zip Slip)
- Сетевые HTTP-запросы без внешних зависимостей (urllib.request)
- Поиск файлов (glob) и содержимого (grep / regex)
- Управление файлами и папками (os, shutil): copy, move, delete, mkdir
- Запуск команд через shell текущей ОС (subprocess) и сбор сведений о системе
"""

import ast
import datetime
import difflib
import fnmatch
import glob
import hashlib
import ipaddress
import logging
import os
import platform
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from tool_providers._vendor.restrictions import restrictions_lifted
from tool_providers._vendor.wikifunctions import clamp_integer

logger = logging.getLogger("logos.tool_providers.filesystem")

BASE_DIR = os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKUP_DIR = os.path.join(BASE_DIR, ".safe_backups")
# Basenames of credential and memory stores that no tool may read or write, whatever their directory.
_PROTECTED_NAME_RE = re.compile(
    r"(?:\.memory(?:\.lock)?|\.pem|\.key|\.p12)$|^id_(?:rsa|ed25519|ecdsa)"
)


def _refuse_protected(target: str) -> None:
    """Dot-files and directories (.env, .git, backups) and credential stores are refused."""
    if restrictions_lifted():
        return
    parts = os.path.relpath(target, BASE_DIR).split(os.sep)
    if any(
        part.startswith(".") and part != "." for part in parts
    ) or _PROTECTED_NAME_RE.search(parts[-1]):
        raise PermissionError(
            "Доступ запрещен: служебные и секретные файлы недоступны инструментам"
        )


def _get_abs_path(path: str) -> str:
    """Нормализует путь и блокирует атаки Path Traversal за пределы проекта."""
    if not path or path.strip() in (".", "./"):
        return BASE_DIR
    if restrictions_lifted():
        # Unrestricted: absolute paths and paths outside the project are accepted as given.
        return os.path.realpath(
            path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
        )
    clean = os.path.normpath(path).lstrip("/")
    target = os.path.realpath(os.path.join(BASE_DIR, clean))
    try:
        common = os.path.commonpath([BASE_DIR, target])
        if common != BASE_DIR:
            raise PermissionError(
                "Доступ запрещен: выход за пределы директории проекта"
            )
    except ValueError:
        raise PermissionError("Доступ запрещен: недопустимый путь к файлу") from None
    _refuse_protected(target)
    return target


def _get_entry_path(path: str) -> str:
    """Like _get_abs_path, but a symlink in the final component is kept, not resolved to its target."""
    head, tail = os.path.split(os.path.normpath(path))
    if tail in ("", ".", ".."):
        return _get_abs_path(path)
    return os.path.join(_get_abs_path(head), tail)


# Public name for other units; they reach it through their host ports.
resolve_tool_path = _get_abs_path


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
        # An unknown whence is refused, not read from the start (#1667).
        if isinstance(raw_whence, str):
            w_norm = raw_whence.lower().strip()
            if w_norm in ("end", "seek_end", "2"):
                whence = os.SEEK_END
            elif w_norm in ("cur", "current", "seek_cur", "1"):
                whence = os.SEEK_CUR
            elif w_norm in ("", "set", "start", "seek_set", "0"):
                whence = os.SEEK_SET
            else:
                return {"error": "Параметр 'whence' должен быть set, cur или end"}
        else:
            try:
                whence = int(raw_whence)
            except (ValueError, TypeError):
                return {"error": "Параметр 'whence' должен быть set, cur или end"}
            if whence not in (os.SEEK_SET, os.SEEK_CUR, os.SEEK_END):
                return {"error": "Параметр 'whence' должен быть set, cur или end"}

        try:
            offset = int(args.get("offset", 0))
        except (ValueError, TypeError):
            return {"error": "Параметр 'offset' должен быть целым числом"}

        max_chunk = 64 * 1024
        raw_length = args.get("length") or args.get("size") or args.get("count")
        try:
            length = int(raw_length) if raw_length is not None else None
        except (ValueError, TypeError):
            length = None

        with open(abs_path, "rb") as f:
            if whence == os.SEEK_END:
                read_bytes = (
                    length
                    if (length is not None and length > 0)
                    else min(file_size, max_chunk)
                )
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
                "lines_count": len(content.splitlines()),
            }
    except Exception as e:
        logger.exception(f"[FS] Ошибка чтения файла {path}: {e}")
        return {"error": f"Ошибка чтения: {str(e)}"}


def write_file(args: dict) -> dict:
    """Безопасная запись файла с валидацией синтаксиса Python и автобэкапом."""
    path = args.get("path") or args.get("file_path") or args.get("filename")
    content = (
        args.get("content")
        if "content" in args
        else (args.get("text") or args.get("code"))
    )
    if not path:
        return {"error": "Параметр 'path' обязателен"}
    if content is None:
        return {"error": "Параметр 'content' обязателен"}

    tmp_path = None
    try:
        abs_path = _get_abs_path(path)
        target_dir = os.path.dirname(abs_path)
        os.makedirs(target_dir, exist_ok=True)
        existing_mode = (
            stat.S_IMODE(os.stat(abs_path).st_mode)
            if os.path.exists(abs_path)
            else None
        )

        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=target_dir,
            delete=False,
            prefix=".tmp_",
            suffix=".tmp",
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        if abs_path.endswith(".py"):
            verify = subprocess.run(
                [
                    "python3",
                    "-c",
                    "import sys; compile(open(sys.argv[1], 'rb').read(), sys.argv[1], 'exec')",
                    tmp_path,
                ],
                capture_output=True,
                text=True,
            )
            if verify.returncode != 0:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                err_msg = verify.stderr.strip() or verify.stdout.strip()
                logger.error(f"[FS] Ошибка синтаксиса при записи в {path}: {err_msg}")
                return {
                    "success": False,
                    "error": f"Файл не записан: синтаксическая ошибка Python:\n{err_msg}",
                    "reverted": True,
                }

        old_content = ""
        backup_file = None
        backup_warning = None
        if os.path.isfile(abs_path):
            try:
                with open(abs_path, encoding="utf-8", errors="replace") as f:
                    old_content = f.read()
                # Created on first use: importing this module must not create directories.
                os.makedirs(BACKUP_DIR, exist_ok=True)
                rel_name = os.path.relpath(abs_path, BASE_DIR).replace(os.sep, "__")
                stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                backup_file = os.path.join(BACKUP_DIR, f"{rel_name}.{stamp}.bak")
                shutil.copy2(abs_path, backup_file)
            except Exception as be:
                backup_file = None
                backup_warning = f"Бэкап не создан: {be}"
                logger.warning(f"[FS] Не удалось создать бэкап {path}: {be}")

        if (
            existing_mode is not None
            and stat.S_IMODE(os.stat(tmp_path).st_mode) != existing_mode
        ):
            os.chmod(tmp_path, existing_mode)

        try:
            os.replace(tmp_path, abs_path)
        except OSError as err:
            if err.errno == 18:
                shutil.move(tmp_path, abs_path)
            else:
                raise

        diff = list(
            difflib.unified_diff(
                old_content.splitlines(),
                content.splitlines(),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )

        result = {
            "success": True,
            "path": path,
            "message": (
                f"Файл '{path}' успешно проверен и сохранен."
                if abs_path.endswith(".py")
                else f"Файл '{path}' сохранен."
            ),
            "diff": "\n".join(diff) if diff else "(файл создан заново)",
            "backup": (
                os.path.relpath(backup_file, BASE_DIR) if backup_file else None
            ),
        }
        if backup_warning:
            result["warning"] = backup_warning
        return result
    except Exception as e:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        logger.exception(f"[FS] Ошибка записи файла {path}: {e}")
        return {"error": f"Ошибка записи: {str(e)}"}


def apply_patch(args: dict, cfg: dict = None) -> dict:
    """Точечно заменить участок текста в файле на новый."""
    path = args.get("file_path") or args.get("path")
    search_text = args.get("search_text")
    replace_text = args.get("replace_text") or args.get("patch") or args.get("diff")
    if not path or not search_text:
        return {
            "success": False,
            "error": "Параметры 'path' и 'search_text' обязательны",
        }
    try:
        abs_path = _get_abs_path(path)
        if not os.path.isfile(abs_path):
            return {"success": False, "error": f"Файл не найден: {path}"}

        try:
            with open(abs_path, encoding="utf-8", newline="") as f:
                content = f.read()
        except UnicodeDecodeError:
            return {
                "success": False,
                "error": "Файл не в кодировке UTF-8: патч не применен",
            }

        if search_text not in content:
            return {
                "success": False,
                "error": "Искомый текст (search_text) не найден в файле.",
            }

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

        with open(abs_path, encoding="utf-8", errors="replace") as f:
            code = f.read()

        try:
            tree = ast.parse(code, filename=path)
        except SyntaxError as se:
            return {
                "success": False,
                "error": f"Синтаксическая ошибка на строке {se.lineno}: {se.msg}",
            }

        classes = []
        functions = []
        imports = []

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                methods = []
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        args_list = [a.arg for a in sub.args.args]
                        methods.append(
                            {"name": sub.name, "line": sub.lineno, "args": args_list}
                        )
                classes.append(
                    {
                        "name": node.name,
                        "line": node.lineno,
                        "docstring": ast.get_docstring(node) or "",
                        "methods": methods,
                    }
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args_list = [a.arg for a in node.args.args]
                functions.append(
                    {
                        "name": node.name,
                        "line": node.lineno,
                        "args": args_list,
                        "docstring": ast.get_docstring(node) or "",
                    }
                )
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
            "functions": functions,
        }
    except Exception as e:
        return {"error": f"Ошибка AST-анализа: {str(e)}"}


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

        with open(abs_path, "rb") as f:
            hasher = hashlib.file_digest(f, getattr(hashlib, algo_name))

        return {
            "success": True,
            "path": path,
            "algorithm": algo_name,
            "digest": hasher.hexdigest(),
            "size_bytes": os.path.getsize(abs_path),
        }
    except Exception as e:
        return {"error": f"Ошибка вычисления хеша: {str(e)}"}


def zip_compress(args: dict) -> dict:
    """Создание ZIP-архива файлов или каталога."""
    source_path = args.get("source_path")
    out_archive = args.get("archive_path")
    if not source_path or not out_archive:
        return {"error": "Параметры 'source_path' и 'archive_path' обязательны"}

    tmp_path = None
    try:
        abs_src = _get_abs_path(source_path)
        abs_out = _get_abs_path(out_archive)
        if not os.path.exists(abs_src):
            return {"error": f"Исходный путь '{source_path}' не существует"}
        if abs_src == abs_out or (
            os.path.exists(abs_out) and os.path.samefile(abs_src, abs_out)
        ):
            return {"error": "Исходный путь и путь архива должны различаться"}

        os.makedirs(os.path.dirname(abs_out), exist_ok=True)
        output_mode = (
            stat.S_IMODE(os.stat(abs_out).st_mode) if os.path.exists(abs_out) else None
        )
        with tempfile.NamedTemporaryFile(
            dir=os.path.dirname(abs_out),
            prefix=".tmp_archive_",
            suffix=".zip",
            delete=False,
        ) as tmp:
            tmp_path = tmp.name
        # Build beside the destination so failures leave an existing archive
        # intact. Neither the temporary file nor the old archive is an input.
        excluded_paths = {os.path.realpath(abs_out), os.path.realpath(tmp_path)}
        excluded_ids = set()
        for excluded_path in (abs_out, tmp_path):
            if os.path.exists(excluded_path):
                info = os.stat(excluded_path)
                excluded_ids.add((info.st_dev, info.st_ino))
        count = 0
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if os.path.isfile(abs_src):
                zf.write(abs_src, arcname=os.path.basename(abs_src))
                count = 1
            else:
                for root, _, files in os.walk(abs_src):
                    for file in files:
                        full_f = os.path.join(root, file)
                        if os.path.realpath(full_f) in excluded_paths:
                            continue
                        info = os.stat(full_f)
                        if (info.st_dev, info.st_ino) in excluded_ids:
                            continue
                        rel_f = os.path.relpath(full_f, abs_src)
                        zf.write(full_f, arcname=rel_f)
                        count += 1

        if (
            output_mode is not None
            and stat.S_IMODE(os.stat(tmp_path).st_mode) != output_mode
        ):
            os.chmod(tmp_path, output_mode)
        os.replace(tmp_path, abs_out)
        tmp_path = None
        return {
            "success": True,
            "archive_path": out_archive,
            "files_archived": count,
            "archive_size_bytes": os.path.getsize(abs_out),
        }
    except Exception as e:
        return {"error": f"Ошибка создания архива: {str(e)}"}
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


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
            "files": extracted[:50],
        }
    except Exception as e:
        return {"error": f"Ошибка распаковки архива: {str(e)}"}


def _resolves_to_link_local(url: str) -> bool:
    """True when the URL host resolves to a link-local address, such as the cloud metadata endpoint."""
    host = urllib.parse.urlsplit(url).hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            if ipaddress.ip_address(info[4][0].split("%")[0]).is_link_local:
                return True
        except ValueError:
            continue
    return False


# Public name for other units; they reach it through their host ports.
resolves_to_link_local = _resolves_to_link_local


class _LinkLocalGuardRedirect(urllib.request.HTTPRedirectHandler):
    """Checks every redirect target, so a public URL cannot bounce the fetch to a link-local address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith(("http://", "https://")) or _resolves_to_link_local(
            newurl
        ):
            raise urllib.error.URLError("redirect to a blocked address")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_GUARDED_OPENER = urllib.request.build_opener(_LinkLocalGuardRedirect)


def http_fetch(args: dict) -> dict:
    """Простой HTTP GET-запрос через стандартный urllib."""
    url = args.get("url")
    if not url:
        return {"error": "Параметр 'url' обязателен"}
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"error": "URL должен начинаться с http:// или https://"}
    if _resolves_to_link_local(url):
        return {"error": "Адреса link-local, включая метаданные облака, недоступны"}

    try:
        timeout = clamp_integer(1, int(args.get("timeout", 10)), 300)
    except (TypeError, ValueError):
        return {"error": "Параметр 'timeout' должен быть целым числом"}
    max_bytes = 256 * 1024

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "LogosPro-StdLib/1.0", "Accept": "*/*"}
        )
        with _GUARDED_OPENER.open(req, timeout=timeout) as resp:
            # One byte past the read size tells whether the 256 KB read cut the body.
            raw = resp.read(max_bytes + 1)
            truncated = len(raw) > max_bytes
            raw = raw[:max_bytes]
            status_code = resp.getcode()
            headers = dict(resp.info())
            try:
                total_size = int(headers.get("Content-Length"))
            except (TypeError, ValueError):
                total_size = None

            text = raw.decode("utf-8", errors="replace")
            return {
                "success": True,
                "url": url,
                "status": status_code,
                "bytes_received": len(raw),
                "truncated": truncated,
                "total_size": total_size,
                "headers": headers,
                "body": text,
            }
    except urllib.error.HTTPError as he:
        return {
            "success": False,
            "status": he.code,
            "error": f"HTTP Error {he.code}: {he.reason}",
        }
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
        return {
            "success": True,
            "path": path,
            "message": f"Директория '{path}' создана.",
        }
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
        # A move relocates the entry itself: a symlink is relinked, not its target moved.
        abs_src = _get_entry_path(src) if action == "move" else _get_abs_path(src)
        abs_dst = _get_abs_path(dst)
        if not os.path.lexists(abs_src):
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
        entry = _get_entry_path(path)
        if entry == BASE_DIR:
            return {"error": "Запрещено удалять корневую директорию проекта"}
        if ".git" in entry.split(os.sep) or ".git" in abs_path.split(os.sep):
            return {"error": "Запрещено удалять файлы репозитория Git"}
        if not os.path.lexists(entry):
            return {"error": f"Путь '{path}' не найден"}

        if os.path.islink(entry):
            # Remove the link itself; the target it points to must survive.
            os.remove(entry)
            kind = "ссылка"
        elif os.path.isdir(abs_path):
            shutil.rmtree(abs_path)
            kind = "папка"
        else:
            os.remove(abs_path)
            kind = "файл"

        return {
            "success": True,
            "path": path,
            "message": f"{kind.capitalize()} '{path}' удален.",
        }
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
            try:
                size = entry.stat().st_size if not entry.is_dir() else None
            except OSError:
                # A broken symlink has no target to stat; list it without a size.
                size = None
            items.append(
                {
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": size,
                }
            )
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
        for p in sorted(matches):
            rel = os.path.relpath(p, BASE_DIR)
            if not rel.startswith((".", "__pycache__")):
                try:
                    p = _get_abs_path(rel)
                except PermissionError:
                    continue
                results.append(
                    {
                        "path": rel,
                        "is_dir": os.path.isdir(p),
                        "size": os.path.getsize(p) if os.path.isfile(p) else 0,
                    }
                )
        return {
            "success": True,
            "pattern": pattern,
            "count": len(results),
            "matches": results,
        }
    except Exception as e:
        return {"error": f"Ошибка поиска по маске: {str(e)}"}


def grep_search(args: dict) -> dict:
    """Полнотекстовый и регулярный поиск строк по файлам проекта."""
    query = args.get("query")
    if not query:
        return {"error": "Параметр 'query' обязателен"}
    file_pattern = args.get("file_pattern", "*.py")
    try:
        max_matches = max(1, int(args.get("max_matches", 50)))
    except (TypeError, ValueError):
        return {"error": "Параметр 'max_matches' должен быть целым числом"}
    case_sensitive = args.get("case_sensitive", False)

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(query, flags)
    except re.error as err:
        return {"error": f"Невалидное регулярное выражение: {err}"}

    results = []
    try:
        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith((".", "__pycache__", "node_modules"))
            ]
            for file in files:
                if not fnmatch.fnmatch(file, file_pattern):
                    continue
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, BASE_DIR)
                try:
                    file_path = _get_abs_path(rel_path)
                    with open(file_path, encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, start=1):
                            if regex.search(line):
                                results.append(
                                    {
                                        "file": rel_path,
                                        "line": line_num,
                                        "text": line.strip()[:200],
                                    }
                                )
                                if len(results) >= max_matches:
                                    return {
                                        "success": True,
                                        "query": query,
                                        "count": len(results),
                                        "truncated": True,
                                        "matches": results,
                                    }
                except Exception:
                    continue
        return {
            "success": True,
            "query": query,
            "count": len(results),
            "truncated": False,
            "matches": results,
        }
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
            "permissions_octal": oct(st.st_mode)[-3:],
        }
    except Exception as e:
        return {"error": f"Ошибка чтения метаданных: {str(e)}"}


_PROTECTED_COMMAND_RE = re.compile(
    r"\.env|\.git\b|\.safe_backups|\.memory|\.pem\b|\.key\b|\.p12\b|id_(?:rsa|ed25519|ecdsa)"
)


def run_command(args: dict) -> dict:
    """Выполнение команды через shell текущей ОС в рабочей директории проекта."""
    command = args.get("command") or args.get("cmd")
    if not command:
        return {"error": "Параметр 'command' обязателен"}
    # A guard against the obvious paths to secrets, not a sandbox: the shell can still spell them out.
    if _PROTECTED_COMMAND_RE.search(str(command)) and not restrictions_lifted():
        return {
            "error": "Команда обращается к служебным или секретным файлам и не выполняется"
        }
    # No app limit: without a timeout (or with 0) the command runs until it ends (#1722).
    try:
        timeout = int(args.get("timeout") or 0)
    except (TypeError, ValueError):
        return {"error": "Параметр 'timeout' должен быть целым числом"}
    if timeout < 0:
        return {"error": "Параметр 'timeout' не может быть отрицательным"}
    try:
        with subprocess.Popen(
            command,
            shell=True,
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env={
                "PATH": os.defpath,
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                **{
                    key: os.environ[key]
                    for key in ("SystemRoot", "WINDIR")
                    if key in dict(os.environ)
                },
            },
        ) as proc:
            try:
                out, err = proc.communicate(timeout=timeout or None)
            except subprocess.TimeoutExpired:
                # The shell leads its own session: kill its children too, or they outlive the timeout.
                if os.name == "posix":
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass  # the group exited right at the deadline
                else:
                    proc.kill()
                raise
        return {
            "success": proc.returncode == 0,
            "command": command,
            "returncode": proc.returncode,
            "stdout": out.strip(),
            "stderr": err.strip(),
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Превышено время ожидания команды ({timeout} сек)",
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Не удалось выполнить команду: {type(e).__name__}: {e}",
        }


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
            "disk_total_gb": round(total / (1024**3), 2),
            "disk_used_gb": round(used / (1024**3), 2),
            "disk_free_gb": round(free / (1024**3), 2),
        }
    except Exception as e:
        return {"error": f"Ошибка сбора системной информации: {str(e)}"}


TOOL_REGISTRY = {
    "apply_patch": {
        "func": apply_patch,
        "description": "Заменить первое точное вхождение search_text на replace_text в файле; пустой replace_text удаляет вхождение. Требуется подтверждение.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "search_text": {"type": "string", "minLength": 1},
                "replace_text": {"type": "string"},
            },
            "required": ["path", "search_text", "replace_text"],
            "additionalProperties": False,
        },
    },
    "read_file": {
        "func": read_file,
        "description": "Позиционированное чтение файла в стиле Си (fseek + fread). Позволяет читать хвосты логов (whence='end') и фрагменты произвольной длины без перегрузки памяти.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к файлу"},
                "offset": {
                    "type": "integer",
                    "description": "Смещение байт (при whence='end' отступает назад от конца)",
                },
                "length": {
                    "type": "integer",
                    "description": "Размер буфера чтения в байтах (по умолчанию 64KB)",
                },
                "whence": {
                    "type": ["string", "integer"],
                    "description": "Точка отсчета: 0/'start' (SEEK_SET), 1/'cur' (SEEK_CUR), 2/'end' (SEEK_END для tail)",
                },
            },
            "required": ["path"],
        },
        "requires_approval": False,
    },
    "write_file": {
        "func": write_file,
        "description": "Безопасная запись файла с валидацией синтаксиса Python перед сохранением.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к файлу"},
                "content": {"type": "string", "description": "Новое содержимое файла"},
            },
            "required": ["path", "content"],
        },
        "requires_approval": True,
    },
    "python_ast_outline": {
        "func": python_ast_outline,
        "description": "Быстрый анализ структуры Python-файла: список классов, функций, методов с аргументами и импортов без выполнения кода.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Путь к .py файлу для анализа структуры",
                }
            },
            "required": ["path"],
        },
        "requires_approval": False,
    },
    "calculate_hash": {
        "func": calculate_hash,
        "description": "Вычислить контрольную сумму файла (sha256, md5, sha1) для проверки целостности.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к файлу"},
                "algorithm": {
                    "type": "string",
                    "enum": ["sha256", "md5", "sha1"],
                    "description": "Алгоритм хеширования",
                },
            },
            "required": ["path"],
        },
        "requires_approval": False,
    },
    "zip_compress": {
        "func": zip_compress,
        "description": "Сжать файл или каталог в ZIP-архив.",
        "parameters": {
            "type": "object",
            "properties": {
                "source_path": {
                    "type": "string",
                    "description": "Исходный файл или папка",
                },
                "archive_path": {
                    "type": "string",
                    "description": "Путь к создаваемому .zip файлу",
                },
            },
            "required": ["source_path", "archive_path"],
        },
        "requires_approval": True,
    },
    "zip_extract": {
        "func": zip_extract,
        "description": "Распаковать ZIP-архив в указанную директорию.",
        "parameters": {
            "type": "object",
            "properties": {
                "archive_path": {"type": "string", "description": "Путь к архиву .zip"},
                "target_dir": {
                    "type": "string",
                    "description": "Директория назначения (по умолчанию '.')",
                },
            },
            "required": ["archive_path"],
        },
        "requires_approval": True,
    },
    "http_fetch": {
        "func": http_fetch,
        "description": "Выполнить базовый HTTP GET-запрос по URL и получить код ответа и текст без внешних зависимостей.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL адрес (http/https)"},
                "timeout": {
                    "type": "integer",
                    "description": "Таймаут в секундах (по умолчанию 10)",
                },
            },
            "required": ["url"],
        },
        "requires_approval": True,
    },
    "make_directory": {
        "func": make_directory,
        "description": "Создать новую директорию в проекте (включая промежуточные папки).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к создаваемой папке"}
            },
            "required": ["path"],
        },
        "requires_approval": True,
    },
    "copy_or_move_file": {
        "func": copy_or_move_file,
        "description": "Скопировать или переместить/переименовать файл или папку.",
        "parameters": {
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "Исходный путь"},
                "dst": {"type": "string", "description": "Целевой путь"},
                "action": {
                    "type": "string",
                    "enum": ["copy", "move"],
                    "description": "Действие: 'copy' или 'move'",
                },
            },
            "required": ["src", "dst"],
        },
        "requires_approval": True,
    },
    "delete_path": {
        "func": delete_path,
        "description": "Удалить файл или папку в проекте.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Путь к удаляемому файлу или директории",
                }
            },
            "required": ["path"],
        },
        "requires_approval": True,
    },
    "list_directory": {
        "func": list_directory,
        "description": "Получение списка файлов и папок в директории проекта.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Путь к директории ('.' по умолчанию)",
                }
            },
            "required": [],
        },
        "requires_approval": False,
    },
    "glob_search": {
        "func": glob_search,
        "description": "Поиск файлов по шаблону маски (glob), например '*.py' или 'logs/*.txt'.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Шаблон поиска (например '*.py')",
                }
            },
            "required": ["pattern"],
        },
        "requires_approval": False,
    },
    "grep_search": {
        "func": grep_search,
        "description": "Поиск текста или регулярного выражения во всех файлах проекта с номерами строк.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Текст или regex для поиска",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Маска файлов (по умолчанию '*.py')",
                },
                "max_matches": {
                    "type": "integer",
                    "description": "Максимум совпадений (по умолчанию 50)",
                },
            },
            "required": ["query"],
        },
        "requires_approval": False,
    },
    "file_stat": {
        "func": file_stat,
        "description": "Получить метаданные файла (размер в байтах, дата создания, дата изменения, права доступа).",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Путь к файлу"}},
            "required": ["path"],
        },
        "requires_approval": False,
    },
    "run_command": {
        "func": run_command,
        "description": "Выполнить команду через shell текущей ОС в рабочей директории проекта и получить exit code, stdout и stderr. Среда определяется хостом выполнения.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Команда для терминала"},
                "timeout": {
                    "type": "integer",
                    "description": "Таймаут выполнения в секундах; без параметра или 0 — без лимита",
                },
            },
            "required": ["command"],
        },
        "requires_approval": True,
    },
    "get_system_info": {
        "func": get_system_info,
        "description": "Получить данные о дисковом пространстве, текущей ОС, архитектуре хоста, директории проекта и версии Python.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_approval": False,
    },
}

READ_ONLY_TOOL_NAMES = frozenset(
    {
        "read_file",
        "python_ast_outline",
        "calculate_hash",
        "list_directory",
        "glob_search",
        "grep_search",
        "file_stat",
        "get_system_info",
    }
)
for _read_tool_name in READ_ONLY_TOOL_NAMES:
    TOOL_REGISTRY[_read_tool_name]["read_only"] = True

FILESYSTEM_TOOLS = TOOL_REGISTRY


def execute_fs_tool(tool_name: str, arguments: dict) -> dict:
    """Точка входа для выполнения зарегистрированных инструментов файловой системы."""
    if tool_name not in TOOL_REGISTRY:
        return {"error": f"Неизвестный системный инструмент: {tool_name}"}
    return TOOL_REGISTRY[tool_name]["func"](arguments)
