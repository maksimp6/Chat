"""Chrome discovery, launch with a fresh profile, shutdown and orphan cleanup.

Chrome is optional. ``find_chrome`` looks for it on every call (never cached):
the ``LOGOS_BROWSER_CHROME`` setting first, then ``PATH``, then Playwright's
browser folders. Every launch gets a fresh profile inside the session's
temporary directory, a minimal environment (``HOME`` points into the profile,
so the owner's real browser profile, cookies and logins are never read) and a
process group of its own, so ``terminate`` stops Chrome together with its
renderer and helper processes. ``owner.json`` next to the profile lets a later
Logos process remove what a crashed one left behind (``sweep_orphans``).
"""

from __future__ import annotations

import errno
import glob
import importlib.util
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from browser.errors import (
    E_CHROME_ENV,
    E_CHROME_START,
    E_WEBSOCKETS,
    BrowserTimeout,
    BrowserUnavailable,
    Deadline,
    timeout_message,
)
from browser.redact import redact_text

if TYPE_CHECKING:
    from browser.settings import ChromeOptions

logger = logging.getLogger("logos.browser.chrome")

CHROME_ENV = "LOGOS_BROWSER_CHROME"
PATH_NAMES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome")
PLAYWRIGHT_ENV = "PLAYWRIGHT_BROWSERS_PATH"
PLAYWRIGHT_DEFAULT_ROOT = "/opt/pw-browsers"
WORKDIR_PREFIX = "logos-browser-"
PROFILE_DIRNAME = "chrome-profile"
OWNER_FILE = "owner.json"
FAILED_LOG_NAME = "logos-browser-chrome-failed.log"
# Technical constants: the DevToolsActivePort poll interval and how much of a failed
# launch's log the error message quotes (the whole log is kept in FAILED_LOG_NAME).
_PORT_FILE_POLL = 0.05
_LOG_TAIL_CHARS = 3000
_LOG_READ_BYTES = 1 << 16

_PASSED_ENV = (
    "PATH",
    "LANG",
    "LC_ALL",
    "TZ",
    "TMPDIR",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "PREFIX",
    "ANDROID_ROOT",
    "ANDROID_DATA",
    "FONTCONFIG_PATH",
)
_PROXY_ENV = ("http_proxy", "https_proxy", "no_proxy", "all_proxy")

_CHROME_FLAGS = (
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-sync",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-breakpad",
    "--disable-client-side-phishing-detection",
    "--disable-component-extensions-with-background-pages",
    "--disable-dev-shm-usage",
    "--disable-features=Translate,MediaRouter,OptimizationHints,AutofillServerCommunication",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-search-engine-choice-screen",
    "--metrics-recording-only",
    "--password-store=basic",
    "--use-mock-keychain",
    "--no-service-autorun",
    "--export-tagged-pdf",
    "--force-color-profile=srgb",
    "--hide-scrollbars",
    "--mute-audio",
)

_SOURCE_WORDS = {"env": CHROME_ENV, "path": "PATH", "playwright": "Playwright"}


@dataclass
class ChromeBinary:
    path: str
    source: str  # "env" | "path" | "playwright"
    headless_shell: bool

    def describe(self) -> str:
        return f"{self.path} (найден через {_SOURCE_WORDS.get(self.source, self.source)})"


@dataclass
class LaunchedChrome:
    proc: subprocess.Popen
    pid: int
    workdir: str
    profile_dir: str
    downloads_dir: str
    ws_url: str
    binary: ChromeBinary
    log_path: str


# ---------------------------------------------------------------------------
# Discovery


def _is_executable(path: str | None) -> bool:
    return bool(path) and os.path.isfile(str(path)) and os.access(str(path), os.X_OK)


def _looks_headless_shell(path: str) -> bool:
    name = os.path.basename(path).lower()
    return "headless-shell" in name or "headless_shell" in name


def _revision(name: str, prefix: str) -> int | None:
    match = re.fullmatch(re.escape(prefix) + r"(\d+)", name)
    return int(match.group(1)) if match else None


def _playwright_binary(root: str) -> ChromeBinary | None:
    """Chromium of a Playwright browsers folder (never chromedriver)."""
    direct = os.path.join(root, "chromium")
    if _is_executable(direct):
        return ChromeBinary(path=direct, source="playwright", headless_shell=False)
    try:
        names = os.listdir(root)
    except OSError:
        return None
    for prefix, pattern, executable, shell in (
        ("chromium-", "chrome-linux*", "chrome", False),
        ("chromium_headless_shell-", "chrome-headless-shell-linux*", "chrome-headless-shell", True),
    ):
        revisions = sorted(
            (
                (revision, name)
                for name in names
                if (revision := _revision(name, prefix)) is not None
            ),
            reverse=True,
        )
        for _revision_number, name in revisions:
            for folder in sorted(glob.glob(os.path.join(root, name, pattern))):
                candidate = os.path.join(folder, executable)
                if _is_executable(candidate):
                    return ChromeBinary(path=candidate, source="playwright", headless_shell=shell)
    return None


def find_chrome() -> tuple[ChromeBinary | None, str | None]:
    """The Chrome to use, or (None, problem); evaluated on every call."""
    configured = os.environ.get(CHROME_ENV, "").strip()
    if configured:
        expanded = os.path.expanduser(configured)
        path = expanded if os.path.isabs(expanded) else shutil.which(expanded)
        if path and _is_executable(path):
            return (
                ChromeBinary(path=path, source="env", headless_shell=_looks_headless_shell(path)),
                None,
            )
        return None, E_CHROME_ENV.format(value=configured)
    for name in PATH_NAMES:
        found = shutil.which(name)
        if found and _is_executable(found):
            return (
                ChromeBinary(path=found, source="path", headless_shell=_looks_headless_shell(found)),
                None,
            )
    roots: list[str] = []
    configured_root = os.environ.get(PLAYWRIGHT_ENV, "").strip()
    if configured_root:
        roots.append(configured_root)
    roots.append(PLAYWRIGHT_DEFAULT_ROOT)
    for root in roots:
        if not os.path.isdir(root):
            continue
        binary = _playwright_binary(root)
        if binary is not None:
            return binary, None
    return None, None


def chrome_status() -> tuple[ChromeBinary | None, str | None]:
    """find_chrome plus the websockets package the DevTools connection needs."""
    binary, problem = find_chrome()
    if binary is None:
        return None, problem
    if importlib.util.find_spec("websockets") is None:
        return None, str(E_WEBSOCKETS)
    return binary, None


# ---------------------------------------------------------------------------
# Proxy settings


def proxy_server_flag(proxy: str) -> str:
    """The proxy as Chrome's --proxy-server takes it: without user and password."""
    text = (proxy or "").strip()
    if "@" not in text:
        return text
    if "://" in text:
        try:
            parts = urlsplit(text)
        except ValueError:
            return text.rpartition("@")[2]
        host = parts.netloc.rpartition("@")[2]
        return f"{parts.scheme}://{host}"
    return text.rpartition("@")[2]


def proxy_credentials(proxy: str) -> tuple[str, str] | None:
    """(user, password) of a proxy URL with userinfo, percent-decoded."""
    text = (proxy or "").strip()
    if "@" not in text:
        return None
    authority = text.split("://", 1)[1] if "://" in text else text
    userinfo = authority.rpartition("@")[0]
    if not userinfo:
        return None
    user, _, password = userinfo.partition(":")
    return unquote(user), unquote(password)


# ---------------------------------------------------------------------------
# Launch


def _is_termux() -> bool:
    return "com.termux" in os.environ.get("PREFIX", "")


def _child_env(home: str) -> dict[str, str]:
    """A minimal environment: no DISPLAY, no real HOME; HOME and XDG_* inside the profile."""
    env: dict[str, str] = {}
    for name in _PASSED_ENV:
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    for name in _PROXY_ENV:
        for variant in (name, name.upper()):
            value = os.environ.get(variant)
            if value is not None:
                env[variant] = value
    env["HOME"] = home
    env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    env["XDG_CACHE_HOME"] = os.path.join(home, ".cache")
    env["XDG_DATA_HOME"] = os.path.join(home, ".local", "share")
    return env


def _argv(binary: ChromeBinary, options: ChromeOptions, data_dir: str) -> list[str]:
    argv = [binary.path, "--headless" if binary.headless_shell else "--headless=new"]
    argv.append("--remote-debugging-port=0")
    argv.append(f"--user-data-dir={data_dir}")
    argv.extend(_CHROME_FLAGS)
    width = max(1, int(options.viewport_width))
    height = max(1, int(options.viewport_height))
    argv.append(f"--window-size={width},{height}")
    proxy = str(options.proxy or "").strip()
    if proxy:
        argv.append(f"--proxy-server={proxy_server_flag(proxy)}")
    if options.ignore_tls_errors:
        argv.append("--ignore-certificate-errors")
    geteuid = getattr(os, "geteuid", None)
    if (geteuid is not None and geteuid() == 0) or _is_termux():
        argv.append("--no-sandbox")
    if _is_termux():
        argv.append("--disable-gpu")
    argv.append("about:blank")
    return argv


def _write_owner(profile_dir: str, chrome_pid: int | None) -> None:
    data = {"owner_pid": os.getpid(), "created": time.time(), "chrome_pid": chrome_pid}
    path = os.path.join(profile_dir, OWNER_FILE)
    temporary = path + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(temporary, path)
    except OSError as exc:
        logger.debug("owner file not written: %s", type(exc).__name__)


def _read_port_file(path: str) -> tuple[int, str] | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    if len(lines) < 2:
        return None
    port_text = lines[0].strip()
    browser_path = lines[1].strip()
    if not port_text.isdigit() or not browser_path.startswith("/"):
        return None
    port = int(port_text)
    if not 0 < port < 65536:
        return None
    return port, browser_path


def _log_tail(path: str) -> str:
    """The redacted end of a Chrome log (the error quotes it; the full log stays on disk)."""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _LOG_READ_BYTES))
            raw = handle.read()
    except OSError:
        return ""
    text = redact_text(raw.decode("utf-8", errors="replace")).strip()
    if len(text) > _LOG_TAIL_CHARS:
        text = "…" + text[-_LOG_TAIL_CHARS:]
    return text


def _keep_failed_log(log_path: str) -> str:
    """Move the log of a failed launch out of the profile; returns where it is."""
    target = os.path.join(tempfile.gettempdir(), FAILED_LOG_NAME)
    try:
        shutil.move(log_path, target)
    except OSError as exc:
        logger.debug("failed chrome log not moved: %s", type(exc).__name__)
        return log_path
    return target


def _signal_group(pid: int, signum: int) -> bool:
    """Signal Chrome's process group; False when nothing is left to signal."""
    killpg = getattr(os, "killpg", None)
    try:
        if killpg is not None:
            killpg(pid, signum)
        else:
            os.kill(pid, signum)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        logger.debug("signal %s to chrome group %s failed: %s", signum, pid, type(exc).__name__)
        return False
    return True


def _launch_failed(
    proc: subprocess.Popen,
    binary: ChromeBinary,
    profile_dir: str,
    log_path: str,
    code: int | None,
) -> BrowserUnavailable:
    _signal_group(proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    try:
        proc.wait()
    except Exception:
        pass
    tail = _log_tail(log_path)
    kept = _keep_failed_log(log_path)
    shutil.rmtree(profile_dir, ignore_errors=True)
    logger.warning(
        "chrome did not start (%s): code %s; log: %s",
        binary.path,
        code,
        redact_text(tail),
    )
    return BrowserUnavailable(
        E_CHROME_START.format(
            binary=binary.path,
            code=code if code is not None else "—",
            log_tail=tail or "пусто",
            path=kept,
        )
    )


def launch(binary: ChromeBinary, options: ChromeOptions, workdir: str, deadline: Deadline) -> LaunchedChrome:
    """Start headless Chrome with a fresh profile in ``workdir``; returns once DevTools listens."""
    profile_dir = os.path.join(workdir, PROFILE_DIRNAME)
    # Every launch starts from an empty profile, even after a crash in the same session.
    shutil.rmtree(profile_dir, ignore_errors=True)
    data_dir = os.path.join(profile_dir, "data")
    home = os.path.join(profile_dir, "home")
    downloads_dir = os.path.join(workdir, "downloads")
    for folder in (
        data_dir,
        os.path.join(home, ".config"),
        os.path.join(home, ".cache"),
        os.path.join(home, ".local", "share"),
        downloads_dir,
    ):
        os.makedirs(folder, exist_ok=True)
    port_file = os.path.join(data_dir, "DevToolsActivePort")
    log_path = os.path.join(profile_dir, "chrome.log")
    argv = _argv(binary, options, data_dir)
    _write_owner(profile_dir, None)
    try:
        with open(log_path, "wb") as log_handle:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log_handle,
                cwd=profile_dir,
                env=_child_env(home),
                start_new_session=True,
                close_fds=True,
            )
    except OSError as exc:
        shutil.rmtree(profile_dir, ignore_errors=True)
        reason = redact_text(f"{type(exc).__name__}: {exc.strerror or exc}")
        logger.warning("chrome did not start (%s): %s", binary.path, reason)
        raise BrowserUnavailable(
            E_CHROME_START.format(
                binary=binary.path, code=exc.errno if exc.errno is not None else "—", log_tail=reason, path="—"
            )
        ) from None
    _write_owner(profile_dir, proc.pid)
    logger.debug("chrome starting: pid %s, binary %s", proc.pid, binary.path)
    while True:
        found = _read_port_file(port_file)
        if found is not None:
            port, browser_path = found
            ws_url = f"ws://127.0.0.1:{port}{browser_path}"
            logger.debug("chrome pid %s listens on 127.0.0.1:%s", proc.pid, port)
            return LaunchedChrome(
                proc=proc,
                pid=proc.pid,
                workdir=workdir,
                profile_dir=profile_dir,
                downloads_dir=downloads_dir,
                ws_url=ws_url,
                binary=binary,
                log_path=log_path,
            )
        code = proc.poll()
        if code is not None:
            raise _launch_failed(proc, binary, profile_dir, log_path, code)
        if deadline.expired():
            launched = LaunchedChrome(
                proc=proc,
                pid=proc.pid,
                workdir=workdir,
                profile_dir=profile_dir,
                downloads_dir=downloads_dir,
                ws_url="",
                binary=binary,
                log_path=log_path,
            )
            terminate(launched)
            raise BrowserTimeout(timeout_message(deadline.seconds, "запуск Chrome"))
        pause = _PORT_FILE_POLL
        remaining = deadline.remaining()
        if remaining is not None:
            pause = min(pause, max(remaining, 0.001))
        time.sleep(pause)


def terminate(launched: LaunchedChrome, grace: float = 5.0) -> list[str]:
    """Stop Chrome and its helpers (SIGTERM, then SIGKILL), reap it and remove the profile."""
    notes: list[str] = []
    proc = launched.proc
    kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
    if proc.poll() is None:
        _signal_group(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            notes.append(f"Chrome не завершился за {grace:g} с — остановлен принудительно")
            _signal_group(proc.pid, kill_signal)
            try:
                proc.wait()
            except Exception as exc:
                logger.debug("chrome pid %s not reaped: %s", proc.pid, type(exc).__name__)
    else:
        # The main process is gone; renderers and helpers of its group may remain.
        _signal_group(proc.pid, kill_signal)
        try:
            proc.wait()
        except Exception:
            pass
    shutil.rmtree(launched.profile_dir, ignore_errors=True)
    logger.debug("chrome pid %s stopped, profile removed", proc.pid)
    return notes


# ---------------------------------------------------------------------------
# Orphans


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _cmdline(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            return handle.read().replace(b"\x00", b" ").decode("utf-8", errors="replace")
    except OSError:
        return None


def sweep_orphans() -> list[str]:
    """Remove Chrome profiles whose Logos process is gone, stopping their Chrome first.

    A profile whose owner is alive (this process included) is left alone, and so
    is one whose Chrome still runs but cannot be confirmed as that profile's
    Chrome. Downloads of the dead session stay in its folder.
    """
    swept: list[str] = []
    pattern = os.path.join(tempfile.gettempdir(), WORKDIR_PREFIX + "*", PROFILE_DIRNAME, OWNER_FILE)
    for owner_path in glob.glob(pattern):
        profile_dir = os.path.dirname(owner_path)
        try:
            with open(owner_path, encoding="utf-8") as handle:
                data = json.load(handle)
            owner_pid = int(data.get("owner_pid") or 0)
            chrome_pid = int(data.get("chrome_pid") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if owner_pid == os.getpid() or _pid_alive(owner_pid):
            continue
        if chrome_pid and _pid_alive(chrome_pid):
            command = _cmdline(chrome_pid)
            if command is None:
                continue
            if profile_dir in command:
                _signal_group(chrome_pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        shutil.rmtree(profile_dir, ignore_errors=True)
        workdir = os.path.dirname(profile_dir)
        # Only empty folders go: downloads of the dead session are kept.
        for folder in (os.path.join(workdir, "downloads"), workdir):
            try:
                os.rmdir(folder)
            except OSError:
                pass
        swept.append(profile_dir)
        logger.debug("orphan chrome profile removed: %s", profile_dir)
    return swept


__all__ = [
    "CHROME_ENV",
    "PATH_NAMES",
    "ChromeBinary",
    "LaunchedChrome",
    "chrome_status",
    "find_chrome",
    "launch",
    "proxy_credentials",
    "proxy_server_flag",
    "sweep_orphans",
    "terminate",
]
