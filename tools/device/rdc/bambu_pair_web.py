#!/usr/bin/env python3
"""Short-lived LAN-only pairing page for the A1 mini camera (one code entry)."""

import html
import ipaddress
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import secrets
import socket
import sys
import time
from urllib.parse import urlsplit
from bambu_camera_snapshot import CODE_FILE, private_write, auth_packet, snapshot, OUTPUT, PIN_FILE

HOST = "192.168.31.29"
PORT = 8765
SESSION = secrets.token_urlsafe(24)
VALID_UNTIL = time.monotonic() + 900
TRIES = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, text, status=200):
        # Plain-text answers get a minimal page with a viewport, so they are not drawn at desktop size.
        if not text.startswith("<"):
            text = (
                "<!doctype html><html lang=ru><meta charset=utf-8>"
                "<meta name=viewport content=\"width=device-width,initial-scale=1\">"
                "<body style='font:20px system-ui;padding:20px'>" + html.escape(text) + "</body></html>"
            )
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(data)

    def allowed(self):
        try:
            ip = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        return ip in ipaddress.ip_network("192.168.31.0/24") and time.monotonic() < VALID_UNTIL

    def do_GET(self):
        if not self.allowed() or self.path not in ("/", "/pair/" + SESSION):
            return self._reply("Страница недоступна", 404)
        self._reply("""<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>A1 mini — камера</title>
<style>body{font:18px system-ui;background:#171c1a;color:#f3f8f5;color-scheme:dark;max-width:480px;margin:35px auto;padding:20px}input,button{box-sizing:border-box;width:100%;font:22px system-ui;padding:15px;margin:12px 0;border-radius:12px}button{background:#29bd75;border:0;color:#071610;font-weight:700}small{color:#adbfb4}input{background:#222b27;color:#f3f8f5;border:1px solid #5d6f64}input::placeholder{color:#9fb3a7}</style>
<h2>📷 Камера A1 mini</h2><p>Введи 8 символов Access Code с экрана принтера (буквы и цифры). Буфер обмена не нужен.</p>
<form method=post action="/"><input type=password name=code inputmode=text autocapitalize=characters spellcheck=false minlength=8 maxlength=8 autocomplete=off required placeholder="Access Code"><button>Подключить камеру</button></form>
<small>Один раз · только домашняя сеть · код сохраняется локально на Redmi 9. Эта временная страница работает по HTTP, без шифрования; используй только доверенную Wi-Fi-сеть.</small></html>""")

    def do_POST(self):
        global TRIES
        if not self.allowed() or self.path not in ("/", "/pair/" + SESSION) or TRIES >= 3:
            return self._reply("Ссылка устарела или недоступна", 403)
        if (
            self.headers.get("Content-Type", "").split(";")[0]
            != "application/x-www-form-urlencoded"
        ):
            return self._reply("Некорректный запрос", 415)
        from urllib.parse import parse_qs

        n = int(self.headers.get("Content-Length", "0"))
        if n <= 0 or n > 200:
            return self._reply("Некорректный размер данных", 400)
        raw = self.rfile.read(n).decode("ascii", errors="replace")
        code = parse_qs(raw).get("code", [""])[0]
        TRIES += 1
        try:
            auth_packet(code)
        except Exception:
            return self._reply("Нужны ровно 8 цифр из LAN Access Code", 400)
        private_write(CODE_FILE, (code + "\n").encode())
        try:
            length = snapshot("192.168.31.12", OUTPUT, CODE_FILE, PIN_FILE)
            print("CAMERA_SNAPSHOT_OK bytes=" + str(length), flush=True)
            self._reply(
                "<html lang=ru><meta name=viewport content='width=device-width'><body style='font:22px system-ui;padding:20px;max-width:36em;margin:0 auto'>✅ Камера подключена, первый кадр сохранён на Redmi 9. Вернись в ChatGPT и напиши «готово».</body></html>"
            )
        except Exception as exc:
            print("CAMERA_SNAPSHOT_UNAVAILABLE " + type(exc).__name__ + ": " + str(exc)[:140], flush=True)
            self._reply(
                "<html lang=ru><meta name=viewport content='width=device-width'><body style='font:20px system-ui;padding:20px;max-width:36em;margin:0 auto'>Код локально сохранён. Камера не отдала кадр. Вернись в ChatGPT и напиши «проверить».</body></html>"
            )


if __name__ == "__main__":
    os.umask(0o077)
    print(f"PAIR_URL=http://{HOST}:{PORT}/pair/{SESSION}", flush=True)
    server = HTTPServer((HOST, PORT), Handler)
    server.timeout = 2
    try:
        while time.monotonic() < VALID_UNTIL and TRIES < 3:
            server.handle_request()
    finally:
        server.server_close()
        print("PAIR_SERVER_STOPPED", flush=True)
