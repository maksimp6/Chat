"""HTTP API of the SSH runtime: settings and a connection test."""

from __future__ import annotations

import logging

from flask import Flask, jsonify, request

from remote_runtime.host import bound, get_current_owner_id
from remote_runtime.ssh import SSHRuntimeError
from remote_runtime.ssh_settings import public_settings, save_settings, test_connection

logger = logging.getLogger("logos.remote_runtime.ssh_routes")

# What the SSH window says for each error: what happened and what to do. The SSH text
# itself goes to the log only.
_DETAILS = {
    "ssh_settings_unreadable": (
        "Не удалось прочитать настройки SSH Runtime. Проверьте сохранённые цели "
        "и known_hosts и сохраните настройки заново."
    ),
    "settings must be an object": (
        "Сервер не получил настройки. Обновите страницу и сохраните снова."
    ),
    "ssh_settings_rejected": (
        "Настройки SSH Runtime не сохранены: проверьте поля формы и JSON целей."
    ),
    "target is required": "Укажите имя цели для проверки.",
    "ssh_runtime_disabled": (
        "SSH Runtime выключен. Включите «SSH Runtime включён», сохраните настройки "
        "и повторите проверку."
    ),
    "ssh_settings_invalid": (
        "Сохранённые настройки SSH Runtime не прошли проверку. "
        "Исправьте JSON целей, сохраните и повторите."
    ),
    "ssh_target_not_found": (
        "Цель с таким именем не найдена. Проверьте имя в поле «Проверить target» "
        "и в JSON целей."
    ),
    "ssh_user_invalid": (
        "Пользователь Linux цели не задан или указан неверно: нужно до 64 символов, "
        "только латинские буквы, цифры, точка, _ и -. Проверьте default_user "
        "и identity_map в JSON цели."
    ),
    "ssh_user_not_allowed": (
        "Пользователь Linux цели не входит в allowed_users. Добавьте его в "
        "allowed_users или укажите другого default_user."
    ),
    "ssh_known_hosts_missing": (
        "Не указан файл known_hosts. Укажите общий known_hosts или known_hosts "
        "в цели, сохраните и повторите."
    ),
    "ssh_host_key_rejected": (
        "Ключа сервера нет в known_hosts, или он изменился. Добавьте актуальный "
        "ключ сервера в файл known_hosts и повторите."
    ),
    "ssh_host_not_found": "Имя сервера не найдено. Проверьте host в JSON цели.",
    "ssh_connection_refused": (
        "Сервер отказал в подключении. Проверьте host и port цели и что на сервере "
        "запущен SSH."
    ),
    "ssh_host_unreachable": (
        "Сервер недоступен по сети. Проверьте host цели и сеть сервера Logos."
    ),
    "ssh_connection_timeout": (
        "Сервер не ответил за время подключения. Проверьте host, port "
        "и доступность сервера."
    ),
    "ssh_identity_file_unavailable": (
        "Файл ключа (identity_file) не найден или не читается на сервере Logos. "
        "Проверьте путь в JSON цели и права файла."
    ),
    "ssh_authentication_failed": (
        "Сервер не принял ключ SSH. Проверьте identity_file цели и что открытый ключ "
        "добавлен в authorized_keys пользователя на сервере."
    ),
    "ssh_client_unavailable": (
        "На сервере Logos не удалось запустить ssh. Установите OpenSSH-клиент "
        "на сервере Logos."
    ),
    "ssh_connection_failed": (
        "Не удалось подключиться по SSH. Проверьте host, port, пользователя "
        "и ключ цели."
    ),
    "ssh_connection_test_failed": (
        "Проверка не выполнена из-за ошибки сервера. Повторите позже; подробности "
        "в журнале сервера."
    ),
}


def get_ssh_runtime_settings():
    try:
        return jsonify(public_settings())
    except bound.TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        logger.exception("Failed to load SSH Runtime settings")
        return jsonify(
            {"error": str(exc), "detail": _DETAILS["ssh_settings_unreadable"]}
        ), 400


def put_ssh_runtime_settings():
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(
                {
                    "error": "settings must be an object",
                    "detail": _DETAILS["settings must be an object"],
                }
            ), 400
        saved = save_settings(data)
        return jsonify({"status": "ok", "settings": saved})
    except bound.TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        logger.exception("Failed to save SSH Runtime settings")
        return jsonify(
            {"error": str(exc), "detail": _DETAILS["ssh_settings_rejected"]}
        ), 400


def test_ssh_runtime_connection():
    try:
        owner_id = get_current_owner_id()
        data = request.get_json(silent=True) or {}
        target = str(data.get("target") or "").strip()
        if not target:
            return jsonify(
                {
                    "error": "target is required",
                    "detail": _DETAILS["target is required"],
                }
            ), 400
        result = test_connection(target, owner_id)
        return jsonify(result)
    except bound.TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except SSHRuntimeError as exc:
        # The cause code and its text go to the window; the SSH text is only logged.
        code = exc.code if exc.code in _DETAILS else "ssh_connection_failed"
        logger.warning("SSH Runtime connection test failed: %s: %s", code, exc)
        return jsonify({"success": False, "error": code, "detail": _DETAILS[code]}), 400
    except Exception:
        logger.exception("SSH Runtime connection test failed")
        return jsonify(
            {
                "success": False,
                "error": "ssh_connection_test_failed",
                "detail": _DETAILS["ssh_connection_test_failed"],
            }
        ), 400


def install(app: Flask) -> None:
    """Serve the SSH runtime API; endpoints keep the view names."""
    app.add_url_rule(
        "/api/ssh-runtime/settings", view_func=get_ssh_runtime_settings, methods=["GET"]
    )
    app.add_url_rule(
        "/api/ssh-runtime/settings", view_func=put_ssh_runtime_settings, methods=["PUT"]
    )
    app.add_url_rule(
        "/api/ssh-runtime/test", view_func=test_ssh_runtime_connection, methods=["POST"]
    )
