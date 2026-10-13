#!/data/data/com.termux/files/usr/bin/python
"""Live Server Android actions bound to a trusted target; no arbitrary shell."""
import argparse
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys

ROOT = pathlib.Path.home() / ".alice-rdc" / "screenshots"
ENV = dict(os.environ, TMPDIR="/data/data/com.termux/files/usr/tmp")
POLICY = pathlib.Path(os.environ.get("ALICE_LIVE_DEVICE_POLICY", str(pathlib.Path.home() / ".alice-rdc" / "live-device.json")))

def adb(*args, binary=False):
    p = subprocess.run(["adb", *map(str, args)], env=ENV, capture_output=True, check=True, timeout=12)
    return p.stdout if binary else p.stdout.decode(errors="replace").strip()

def device():
    """Match a specifically enrolled ADB transport AND its Android ID.

    Both checks run for each operation. A re-pair with a changed ADB serial must
    be explicitly re-enrolled, not silently redirected to another phone.
    """
    try:
        data = json.loads(POLICY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("Trusted target policy unavailable") from error
    if (not isinstance(data, dict)
        or set(data)!={"model","device","serial","android_id"}
        or data.get("model")!="Redmi_9" or data.get("device")!="galahad"
        or not isinstance(data.get("serial"),str)
        or not re.fullmatch(r"[A-Za-z0-9._:-]{5,100}",data["serial"])
        or not isinstance(data.get("android_id"),str)
        or not re.fullmatch(r"[0-9a-f]{16}",data["android_id"])):
        raise RuntimeError("Invalid pinned target policy")
    serial=data["serial"]
    listed=0
    for row in adb("devices","-l").splitlines():
        parts=row.split()
        if len(parts)<3 or parts[0]!=serial or parts[1]!="device":
            continue
        attrs=dict(p.split(":",1) for p in parts[2:] if ":" in p)
        if (attrs.get("model"),attrs.get("device"))==(data["model"],data["device"]):
            listed+=1
    if listed!=1:
        raise RuntimeError("Pinned Redmi 9 transport unavailable")
    actual=adb("-s",serial,"shell","settings","get","secure","android_id").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{16}",actual) or not secrets.compare_digest(actual,data["android_id"]):
        raise RuntimeError("Pinned Redmi 9 identity mismatch")
    return serial

def keyguard_state(window_dump):
    """Return locked/unlocked only from an authoritative explicit boolean.

    Fail closed if Android omits the field or changes the expected format.
    """
    if not isinstance(window_dump,str):
        return True
    values=re.findall(r"\b(?:isKeyguardShowing|mShowingLockscreen|mKeyguardShowing)\s*[=:]\s*(true|false)\b",window_dump,re.I)
    if not values:
        return True
    return any(value.lower()=="true" for value in values)

def action(a):
    serial = device()
    prefix = ["-s", serial]
    # Check again inside the final ADB action adapter, not only in HTTP.
    # A transition into Keyguard must not turn a queued tap into PIN input.
    if a.command not in ("wake", "status", "unlock"):
        if keyguard_state(adb(*prefix, "shell", "dumpsys", "window")):
            raise RuntimeError("Device locked; input denied")
    if a.command == "tap":
        adb(*prefix, "shell", "input", "tap", a.x, a.y)
    elif a.command == "swipe":
        adb(*prefix, "shell", "input", "swipe", a.x, a.y, a.x2, a.y2, a.ms)
    elif a.command == "wake":
        adb(*prefix, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
    elif a.command == "unlock":
        # Request Android's normal dismissal only. Never inject credentials.
        adb(*prefix, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
        adb(*prefix, "shell", "wm", "dismiss-keyguard")
        locked = keyguard_state(adb(*prefix, "shell", "dumpsys", "window"))
        return {"command": "screen.unlock", "verified": not locked,
                "state": "AUTH_REQUIRED" if locked else "UNLOCKED"}
    elif a.command == "back":
        adb(*prefix, "shell", "input", "keyevent", "KEYCODE_BACK")
    elif a.command == "home":
        adb(*prefix, "shell", "input", "keyevent", "KEYCODE_HOME")
    elif a.command == "open":
        if not a.package or not all(c.isalnum() or c in "._" for c in a.package):
            raise ValueError("Invalid package")
        adb(*prefix, "shell", "monkey", "-p", a.package, "1")
    elif a.command == "screenshot":
        ROOT.mkdir(parents=True, exist_ok=True)
        output = ROOT / "latest.jpg"
        png = adb(*prefix, "exec-out", "screencap", "-p", binary=True)
        if not png.startswith(b"\x89PNG"):
            raise RuntimeError("Screenshot is not PNG")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", "pipe:0", "-vf", "scale=1280:-2", "-q:v", "5", "-frames:v", "1", str(output)], input=png, env=ENV, check=True)
        return {"file": str(output), "bytes": output.stat().st_size}
    elif a.command == "status":
        window=adb(*prefix,"shell","dumpsys","window")
        focus=window.split("mCurrentFocus=",1)[-1].splitlines()[0] if "mCurrentFocus=" in window else ""
        return {"device":serial,"target":"Redmi_9","focus":focus,
                "keyguard_locked":keyguard_state(window)}
    return {"command": a.command, "submitted": True, "verified": False}

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["tap", "swipe", "back", "home", "open", "screenshot", "status", "wake", "unlock"])
    p.add_argument("--x", type=int, default=0)
    p.add_argument("--y", type=int, default=0)
    p.add_argument("--x2", type=int, default=0)
    p.add_argument("--y2", type=int, default=0)
    p.add_argument("--ms", type=int, default=300)
    p.add_argument("--package", default="")
    a = p.parse_args()
    try:
        print(json.dumps(action(a), ensure_ascii=False))
    except (RuntimeError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
