#!/usr/bin/env python3
"""Safely repair Redmi 9 Live Server target binding and remove app hardcoding.

Keeps existing bearer authentication, loopback binding and bounded verbs.
No ADB input is issued by this repair script.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import py_compile
import shutil
import tempfile
import time

BASE = Path.home() / ".alice-rdc"
ACTION = BASE / "android_actions.py"
SERVER = BASE / "android_live_server.py"
POLICY = BASE / "live-device.json"


def once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return source.replace(old, new, 1)


def atomic_write(path: Path, content: str) -> None:
    mode = path.stat().st_mode & 0o777
    handle, temp = tempfile.mkstemp(prefix=".alice-repair-", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def patch_actions(source: str) -> str:
    source = once(
        source,
        '"""RDC Android actions over authenticated ADB; no root or arbitrary shell."""',
        '"""Live Server Android actions bound to a trusted target; no arbitrary shell."""',
        "action docstring",
    )
    source = once(
        source,
        'ENV = dict(os.environ, TMPDIR="/data/data/com.termux/files/usr/tmp")',
        'ENV = dict(os.environ, TMPDIR="/data/data/com.termux/files/usr/tmp")\n'
        'POLICY = pathlib.Path.home() / ".alice-rdc" / "live-device.json"',
        "policy path",
    )
    source = once(
        source,
        "capture_output=True, check=True)\n    return p.stdout",
        "capture_output=True, check=True, timeout=12)\n    return p.stdout",
        "adb timeout",
    )
    beginning = source.index("def device():\n")
    end = source.index("\ndef action(a):", beginning)
    replacement = '''def device():
    """Resolve one exact, trusted device even when other ADB phones are online.

    The policy is a private local file, never supplied by an HTTP request.
    Ambiguous/offline/missing targets fail closed; never pick the first device.
    """
    try:
        config = json.loads(POLICY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("Trusted device policy unavailable") from error
    if (not isinstance(config, dict) or set(config) != {"model", "device"}
            or config.get("model") != "Redmi_9"
            or config.get("device") != "galahad"):
        raise RuntimeError("Unexpected device policy")
    matches = []
    for line in adb("devices", "-l").splitlines():
        columns = line.split()
        if len(columns) < 3 or columns[1] != "device":
            continue
        attributes = dict(part.split(":", 1) for part in columns[2:]
                          if ":" in part)
        if (attributes.get("model") == config["model"]
                and attributes.get("device") == config["device"]):
            matches.append(columns[0])
    if len(matches) != 1:
        raise RuntimeError("Trusted Redmi 9 ADB target unavailable or ambiguous")
    return matches[0]
'''
    source = source[:beginning] + replacement + source[end:]
    source = once(
        source,
        '    elif a.command == "back":\n',
        '    elif a.command == "wake":\n'
        '        adb(*prefix, "shell", "input", "keyevent", "KEYCODE_WAKEUP")\n'
        '    elif a.command == "back":\n',
        "safe wake verb",
    )
    source = once(
        source,
        'return {"device": serial, "focus": adb(*prefix, "shell", "dumpsys", "window").split("mCurrentFocus=")[-1].splitlines()[0]}',
        'return {"device": serial, "target": "Redmi_9", "focus": adb(*prefix, "shell", "dumpsys", "window").split("mCurrentFocus=")[-1].splitlines()[0]}',
        "status binding",
    )
    index = source.index("\np = argparse.ArgumentParser()")
    parser = source[index + 1 :]
    parser = once(
        parser,
        'choices=["tap", "swipe", "back", "home", "open", "screenshot", "status"]',
        'choices=["tap", "swipe", "back", "home", "open", "screenshot", "status", "wake"]',
        "wake CLI command",
    )
    parser = once(
        parser,
        "except (RuntimeError, ValueError, subprocess.CalledProcessError) as e:",
        "except (RuntimeError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:",
        "CLI timeout guard",
    )
    source = (
        source[:index]
        + "\ndef main(argv=None):\n"
        + "".join(
            ("    " + line if line.strip() else line) for line in parser.splitlines(keepends=True)
        )
        + '\n\nif __name__ == "__main__":\n    main()\n'
    )
    return source


def patch_server(source: str) -> str:
    source = once(
        source,
        'ALLOWED = {"status", "tap", "swipe", "back", "home", "open", "screenshot"}\nSAFE_APP = "com.mobile.legends"',
        'ALLOWED = {"status", "tap", "swipe", "back", "home", "open", "screenshot", "wake"}\nTRUSTED_TARGET = "Redmi_9"',
        "allowed verbs + app removal",
    )
    source = once(
        source,
        """    def safe_screen(self):
        focus = execute([ACTIONS, "status"]).get("focus") or ""
        return isinstance(focus, str) and (" " + SAFE_APP + "/") in focus
""",
        '''    def safe_screen(self):
        """Authenticate the actual bound device; no app-specific allowlist."""
        status = execute([ACTIONS, "status"])
        serial = status.get("device")
        if (status.get("target") != TRUSTED_TARGET
                or not isinstance(serial, str) or not serial
                or not isinstance(status.get("focus"), str)):
            return False
        # The UI accessibility helper invokes adb without -s; bind it too.
        os.environ["ANDROID_SERIAL"] = serial
        return True
''',
        "device guard",
    )
    source = once(
        source,
        'return self.send(200, {"ok": True, "modes": ["screen", "ocr"]})',
        'return self.send(200, {"ok": True, "modes": ["screen", "ocr", "actions"], '
        '"device_policy": "redmi9_bound", "commands": sorted(ALLOWED)})',
        "health capabilities",
    )
    source = once(
        source,
        """            if path == "/status":
                return self.send(200, execute([ACTIONS, "status"]))
""",
        """            if path == "/status":
                result = execute([ACTIONS, "status"])
                if result.get("target") != TRUSTED_TARGET:
                    return self.send(403, {"error": "wrong_device"})
                os.environ["ANDROID_SERIAL"] = result["device"]
                return self.send(200, result)
""",
        "status device guard",
    )
    source = once(
        source,
        """                if command == "open":
                    if data.get("package") != SAFE_APP:
                        return self.send(403, {"error": "package_not_allowlisted"})
""",
        "",
        "remove app restriction",
    )
    source = source.replace('"screen_not_allowlisted"', '"device_not_authorized"')
    if "SAFE_APP" in source or '"com.mobile.legends"' in source:
        raise RuntimeError("Old app allowlist remains")
    return source


def main() -> None:
    previous = {
        ACTION: ACTION.read_text(encoding="utf-8"),
        SERVER: SERVER.read_text(encoding="utf-8"),
    }
    changes = {ACTION: patch_actions(previous[ACTION]), SERVER: patch_server(previous[SERVER])}
    # Verify syntax before any persistent changes.
    for path, content in changes.items():
        compile(content, str(path), "exec")
    config = {"model": "Redmi_9", "device": "galahad"}
    if POLICY.exists() and json.loads(POLICY.read_text()) != config:
        raise RuntimeError("Existing trusted device policy differs; refusing overwrite")
    backup = BASE / "repairs" / ("live-" + time.strftime("%Y%m%d-%H%M%S"))
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    for path in previous:
        shutil.copy2(path, backup / path.name)
    try:
        for path, content in changes.items():
            atomic_write(path, content)
        if not POLICY.exists():
            fd = os.open(POLICY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(config, stream)
                stream.flush()
                os.fsync(stream.fileno())
        for path in changes:
            py_compile.compile(str(path), doraise=True)
    except BaseException:
        for path in previous:
            atomic_write(path, previous[path])
        raise
    print(
        json.dumps(
            {
                "result": "patched",
                "backup": str(backup),
                "files": [path.name for path in changes],
                "policy": "redmi9_bound",
            }
        )
    )


if __name__ == "__main__":
    main()
