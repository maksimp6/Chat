"""Home page of the Redmi 9 on port 80: the home's own page with the phone's live state.

Run from the Logos checkout in Termux; the script also writes its Termux:Boot hook:

    python scripts/home/home_server.py

Port 80 opens without root because the boot hook ``10-unprivileged-ports`` sets
``net.ipv4.ip_unprivileged_port_start`` to 0. The page shows only the state below;
the Logos server (port 8080) is not exposed through it.
"""

from __future__ import annotations

import html
import ipaddress
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ddns_regru  # noqa: E402 (sibling script)

PORT = int(os.environ.get("HOME_PAGE_PORT", "80"))
BOOT_HOOK = Path.home() / ".termux/boot/70-home-page"
STARTED = time.time()


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    with open("/proc/meminfo", encoding="ascii") as handle:
        for line in handle:
            name, _, rest = line.partition(":")
            values[name] = int(rest.split()[0]) * 1024
    return values


def _logos_online() -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:8080/healthz", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def _gib(value: float) -> str:
    return f"{value / 1024 ** 3:.1f} ГиБ"


def _state() -> dict[str, object]:
    disk = os.statvfs(str(Path.home()))
    memory = _meminfo()
    return {
        "time": time.strftime("%d.%m.%Y %H:%M"),
        "logos": _logos_online(),
        "memory_free": memory.get("MemAvailable", 0),
        "memory_total": memory.get("MemTotal", 0),
        "disk_free": disk.f_bavail * disk.f_frsize,
        "disk_total": disk.f_blocks * disk.f_frsize,
        "page_uptime_hours": round((time.time() - STARTED) / 3600, 1),
    }


PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Дом</title>
<style>
  :root {{ --bg: #f5f3ee; --card: #ffffff; --text: #1f2328; --muted: #6b6f76; --ok: #2f7d4f; --bad: #b3412e; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #16181c; --card: #1f2227; --text: #e8e6e1; --muted: #9a9ea5; --ok: #5fbf85; --bad: #e27a66; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
         font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  main {{ max-width: 720px; margin: 0 auto; padding: 48px 16px; }}
  h1 {{ font-size: 40px; margin: 0 0 4px; letter-spacing: -0.02em; }}
  .sub {{ color: var(--muted); margin: 0 0 32px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }}
  .card {{ background: var(--card); border-radius: 14px; padding: 18px 20px; }}
  .label {{ color: var(--muted); font-size: 14px; }}
  .value {{ font-size: 24px; font-weight: 600; margin-top: 4px; }}
  .ok {{ color: var(--ok); }} .bad {{ color: var(--bad); }}
  footer {{ color: var(--muted); font-size: 13px; margin-top: 32px; }}
</style>
</head>
<body>
<main>
  <h1>Дом</h1>
  <p class="sub">Домашний сервер на Redmi 9 · {time}</p>
  <div class="grid">
    <div class="card"><div class="label">Logos</div>
      <div class="value {logos_class}">{logos_text}</div></div>
    <div class="card"><div class="label">Свободная память</div>
      <div class="value">{memory_free} <span class="label">из {memory_total}</span></div></div>
    <div class="card"><div class="label">Свободное место</div>
      <div class="value">{disk_free} <span class="label">из {disk_total}</span></div></div>
  </div>
  <footer>Страница работает {page_uptime_hours} ч · данные обновляются при каждом открытии · <a href="/ddns" style="color: inherit">DDNS</a></footer>
</main>
</body>
</html>
"""


DDNS_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DDNS</title>
<style>
  :root {{ --bg: #f5f3ee; --card: #ffffff; --text: #1f2328; --muted: #6b6f76; --ok: #2f7d4f; --bad: #b3412e; --line: #d9d6cf; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #16181c; --card: #1f2227; --text: #e8e6e1; --muted: #9a9ea5; --ok: #5fbf85; --bad: #e27a66; --line: #3a3d43; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
         font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  main {{ max-width: 520px; margin: 0 auto; padding: 48px 16px; }}
  h1 {{ font-size: 32px; margin: 0 0 4px; }}
  .sub {{ color: var(--muted); margin: 0 0 24px; }}
  form, .card {{ background: var(--card); border-radius: 14px; padding: 20px; margin-bottom: 16px; }}
  label {{ display: block; color: var(--muted); font-size: 14px; margin: 12px 0 4px; }}
  input {{ width: 100%; font: inherit; padding: 10px 12px; border: 1px solid var(--line);
          border-radius: 10px; background: var(--bg); color: var(--text); }}
  button {{ margin-top: 16px; width: 100%; font: inherit; font-weight: 600; padding: 12px;
           border: 0; border-radius: 10px; background: var(--text); color: var(--bg); cursor: pointer; }}
  .ok {{ color: var(--ok); }} .bad {{ color: var(--bad); }}
  pre {{ white-space: pre-wrap; font-size: 13px; color: var(--muted); margin: 0; }}
  a {{ color: inherit; }}
</style>
</head>
<body>
<main>
  <h1>DDNS</h1>
  <p class="sub">{domain} указывает на дом и обновляется каждые 5 минут</p>
  {message}
  <form method="post" action="/ddns">
    <label for="u">Логин REG.RU</label>
    <input id="u" name="username" autocomplete="username" required>
    <label for="p">Пароль REG.RU</label>
    <input id="p" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Сохранить и обновить</button>
  </form>
  <div class="card"><pre>{log}</pre></div>
  <p class="sub"><a href="/">← Дом</a></p>
</main>
</body>
</html>
"""


def _local(address: str) -> bool:
    """The DDNS form takes the REG.RU password, so it answers only inside the home network."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def _ddns_page(message: str = "") -> bytes:
    try:
        lines = ddns_regru.LOG.read_text(encoding="utf-8").splitlines()[-8:]
    except OSError:
        lines = []
    return DDNS_PAGE.format(
        domain=ddns_regru.DOMAIN,
        message=message,
        log=html.escape("\n".join(lines) or "Записей пока нет"),
    ).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "home"

    def _send(self, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        if self.path != "/ddns" or not _local(self.client_address[0]):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        account = {
            "username": (form.get("username") or [""])[0].strip(),
            "password": (form.get("password") or [""])[0],
        }
        try:
            ddns_regru.save_and_start(account)
            message = f'<div class="card ok">Готово: {ddns_regru.DOMAIN} указывает на этот дом.</div>'
        except Exception as exc:
            message = (
                f'<div class="card bad">Не получилось: {html.escape(str(exc))}<br>'
                f"{html.escape(ddns_regru.HINT)}</div>"
            )
        self._send(_ddns_page(message))

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        state = _state()
        if self.path == "/ddns":
            if not _local(self.client_address[0]):
                self.send_error(404)
                return
            self._send(_ddns_page())
            return
        if self.path == "/state.json":
            body = json.dumps(state).encode("utf-8")
            content_type = "application/json"
        elif self.path in ("/", "/index.html"):
            body = PAGE.format(
                time=html.escape(str(state["time"])),
                logos_class="ok" if state["logos"] else "bad",
                logos_text="работает" if state["logos"] else "не отвечает",
                memory_free=_gib(state["memory_free"]),
                memory_total=_gib(state["memory_total"]),
                disk_free=_gib(state["disk_free"]),
                disk_total=_gib(state["disk_total"]),
                page_uptime_hours=state["page_uptime_hours"],
            ).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def main() -> None:
    script = Path(__file__).resolve()
    BOOT_HOOK.parent.mkdir(parents=True, exist_ok=True)
    BOOT_HOOK.write_text(
        "#!/data/data/com.termux/files/usr/bin/sh\n"
        f"{sys.executable} {script} >>{Path.home()}/.home-page.log 2>&1 </dev/null &\n",
        encoding="utf-8",
    )
    BOOT_HOOK.chmod(0o700)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
