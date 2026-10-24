#!/data/data/com.termux/files/usr/bin/python
"""Local-only ADB pairing and connection panel for Redmi 9."""

from __future__ import annotations

import html
import ipaddress
import secrets
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

HOST, PORT = "127.0.0.1", 18685
ADB = "/data/data/com.termux/files/usr/bin/adb"
TOKEN = secrets.token_hex(24)


def render(ip="192.168.31.109", port="42125", result="", error=False, raw=""):
    ip, port = html.escape(ip, quote=True), html.escape(port, quote=True)
    message = ""
    if result:
        cls = "bad" if error else "good"
        message = f'<div class="msg {cls}" role="status"><pre>{html.escape(result)}</pre></div>'
    if raw:
        # Raw adb output is English and only useful for debugging, so it stays folded.
        message += f"<details><summary>Подробности</summary><pre>{html.escape(raw)}</pre></details>"
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ADB Connect · Redmi 9</title>
<style>
*{{box-sizing:border-box}}body{{font:16px system-ui,Arial;background:#13181d;color:#edf2f8;margin:0;padding:24px 14px}}
main{{max-width:470px;margin:0 auto}}h1{{font-size:25px;margin:8px 0 12px}}
p{{line-height:1.45;color:#c0ccd8}}.route{{background:#202a33;border-radius:12px;padding:14px;margin:16px 0}}
label{{display:block;margin:18px 0 7px;font-weight:650}}input{{width:100%;background:#202a33;color:white;border:1px solid #637381;border-radius:12px;padding:17px;font:inherit;font-size:20px}}
button{{width:100%;border:0;border-radius:12px;font:inherit;font-weight:750;padding:17px;margin-top:14px;background:#88c8ff;color:#071723}}
button.alt{{background:#304355;color:#f3f7fc}}details{{margin-top:22px;border-top:1px solid #485764;padding-top:12px}}
summary{{cursor:pointer;font-weight:650;padding:12px 0;min-height:44px}}small{{color:#c0ccd8}}
.msg{{margin-top:20px;padding:12px;border-radius:10px;background:#163e34;overflow-wrap:anywhere}}.bad{{background:#4a2323}}
pre{{white-space:pre-wrap;font:13px monospace;margin:0}}footer{{font-size:12px;color:#a8b7c7;margin-top:25px}}
</style></head><body><main>
<h1>ADB Connect</h1><div class="route"><b>ADB-клиент:</b> Redmi 9 <small>(Redmi_9)</small><br><b>Целевой телефон:</b> Redmi 15 <small>(25062RN2DY)</small></div>
<p>Введи IP и порт, показанные в беспроводной отладке <b>25062RN2DY</b>.</p>
<form action="/action" method="post" autocomplete="off">
<input type="hidden" name="token" value="{TOKEN}">
<label for="hostname">Hostname / имя целевого телефона</label>
<input id="hostname" name="hostname" value="Redmi 15" maxlength="64" spellcheck="false">
<label for="ip">IP-адрес целевого телефона</label>
<input id="ip" name="ip" value="{ip}" required inputmode="decimal" spellcheck="false" placeholder="192.168.1.100">
<label for="port">Порт подключения</label>
<input id="port" name="port" type="number" min="1" max="65535" value="{port}" required inputmode="numeric">
<button name="action" value="connect">Подключить ADB</button>
<details><summary>Сопряжение по шестизначному коду</summary>
<p><small>В Android: Беспроводная отладка → Подключить устройство с помощью кода. Используй именно порт сопряжения.</small></p>
<label for="code">Код сопряжения</label>
<input id="code" name="code" inputmode="numeric" maxlength="6" pattern="[0-9]{{6}}" placeholder="6 цифр">
<button class="alt" name="action" value="pair">Сопрячь ADB</button></details>
</form>{message}
<footer>Панель доступна только на этом телефоне: 127.0.0.1. Коды не сохраняются.</footer>
</main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def respond(self, code, data, mime="text/html; charset=utf-8"):
        raw = data.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'",
        )
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def valid_host(self):
        return self.headers.get("Host") == f"{HOST}:{PORT}"

    def do_GET(self):
        if not self.valid_host():
            return self.respond(403, "Forbidden", "text/plain; charset=utf-8")
        if self.path == "/health":
            return self.respond(200, "READY", "text/plain; charset=utf-8")
        if self.path != "/":
            return self.respond(404, "Not found", "text/plain; charset=utf-8")
        self.respond(200, render())

    def do_POST(self):
        if not self.valid_host() or self.path != "/action":
            return self.respond(403, "Forbidden", "text/plain; charset=utf-8")
        if self.headers.get("Origin") not in (None, f"http://{HOST}:{PORT}"):
            return self.respond(403, "Forbidden", "text/plain; charset=utf-8")
        try:
            size = int(self.headers.get("Content-Length", "-1"))
            if not 0 <= size <= 2048:
                return self.respond(413, "Request too large", "text/plain; charset=utf-8")
            fields = parse_qs(self.rfile.read(size).decode("utf-8"), max_num_fields=12)
            field = lambda key: fields.get(key, [""])[0].strip()
            # Compare bytes: compare_digest rejects non-ASCII str, which would drop the connection without a reply.
            if not secrets.compare_digest(field("token").encode("utf-8"), TOKEN.encode("utf-8")):
                return self.respond(403, "Invalid token", "text/plain; charset=utf-8")
            ip, port, action, code = field("ip"), field("port"), field("action"), field("code")
            try:
                addr = ipaddress.IPv4Address(ip)
            except ValueError:
                raise ValueError("Укажи IPv4-адрес телефона из настроек отладки") from None
            if not addr.is_private or addr.is_multicast or addr.is_unspecified:
                raise ValueError("Укажи частный IPv4-адрес телефона из настроек отладки")
            if not port.isdecimal() or not 1 <= int(port) <= 65535:
                raise ValueError("Некорректный порт")
            if action == "pair":
                if not (len(code) == 6 and code.isdecimal()):
                    raise ValueError("Для сопряжения нужен шестизначный код")
                cmd, stdin = [ADB, "pair", f"{ip}:{port}"], code + "\n"
            elif action == "connect":
                cmd, stdin = [ADB, "connect", f"{ip}:{port}"], None
            else:
                raise ValueError("Неизвестная операция")
            output = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=22)
            raw = (
                output.stdout.strip()
                or output.stderr.strip()
                or f"ADB exit code: {output.returncode}"
            )
            if output.returncode == 0 and action == "connect":
                devices = subprocess.run(
                    [ADB, "devices", "-l"], capture_output=True, text=True, timeout=8
                )
                raw += "\n\n" + devices.stdout.strip()
            ok = output.returncode == 0
            status = "Операция выполнена." if ok else "Не удалось выполнить операцию. Подробности ниже."
            self.respond(200, render(ip, port, status, not ok, raw[:3000]))
        except (ValueError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                # The exception text names the adb binary path, so the page gets a plain sentence instead.
                reason = "adb не ответил за 22 секунды. Проверьте, что телефон в сети и беспроводная отладка включена."
            elif isinstance(exc, UnicodeDecodeError):
                reason = "Не удалось прочитать запрос."
            else:
                reason = str(exc)
            self.respond(
                200,
                render(
                    fields.get("ip", [""])[0] if "fields" in locals() else "",
                    fields.get("port", [""])[0] if "fields" in locals() else "",
                    f"Ошибка: {reason}",
                    True,
                ),
            )

    def log_message(self, fmt, *args):
        # Never log pairing codes, IP/port submissions, or user input.
        pass


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
