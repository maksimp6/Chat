#!/usr/bin/env python3
"""Capture the real Logos sidebar on the existing Redmi 9 runner."""

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.request import urlopen


SOCKET = "\0io.github.forkmaintainers.iceraven/firefox-debugger-socket"
PACKAGE = "io.github.forkmaintainers.iceraven"
ROOT = "/system/bin/"


def command(*args):
    p = subprocess.run(args, capture_output=True, timeout=20)
    if p.returncode:
        raise RuntimeError("Android command failed: " + Path(args[0]).name)
    return p.stdout


def device_guard():
    if command(ROOT + "getprop", "ro.product.model").strip() != b"Redmi 9":
        raise RuntimeError("The selected device is not Redmi 9")
    policy = command(ROOT + "dumpsys", "window", "policy").decode()
    if not re.search(r"(?m)^\s*showing=false\s*$", policy):
        raise RuntimeError("The screen is locked or its state is unknown")
    if "mInputRestricted=false" not in policy:
        raise RuntimeError("Input is restricted")
    activity = command(ROOT + "dumpsys", "activity", "activities").decode()
    resumed = re.findall(r"(?m)^.*(?:mResumedActivity|topResumedActivity):.*$", activity)
    if not resumed or any(PACKAGE + "/" not in line for line in resumed):
        raise RuntimeError("Iceraven is not the foreground application")


class RDP:
    def __init__(self):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.settimeout(8)
        self.s.connect(SOCKET)
        self.buffer = b""
        self.receive()

    def receive(self):
        while b":" not in self.buffer:
            part = self.s.recv(65536)
            if not part:
                raise RuntimeError("DevTools connection closed")
            self.buffer += part
        size, payload = self.buffer.split(b":", 1)
        size = int(size)
        if size > 4 * 1024 * 1024:
            raise RuntimeError("Unexpected DevTools packet size")
        while len(payload) < size:
            part = self.s.recv(65536)
            if not part:
                raise RuntimeError("Truncated DevTools packet")
            payload += part
        self.buffer = payload[size:]
        return json.loads(payload[:size])

    def send(self, data):
        body = json.dumps(data).encode()
        self.s.sendall(str(len(body)).encode() + b":" + body)

    def request(self, actor, kind):
        self.send({"to": actor, "type": kind})
        for _ in range(200):
            result = self.receive()
            if result.get("from") == actor:
                if result.get("error"):
                    raise RuntimeError("DevTools request failed")
                return result
        raise RuntimeError("Missing DevTools reply")

    def evaluate(self, actor, code):
        self.send({"to": actor, "type": "evaluateJSAsync", "text": code, "eager": False})
        for _ in range(200):
            result = self.receive()
            if result.get("type") == "evaluationResult":
                if result.get("hasException"):
                    raise RuntimeError("The browser action failed")
                value = result.get("result")
                if not isinstance(value, str):
                    raise RuntimeError("Unexpected browser reply")
                return json.loads(value)
            if result.get("error"):
                raise RuntimeError("DevTools evaluation failed")
        raise RuntimeError("Missing browser result")

    def find_logos(self):
        candidates = []
        for tab in self.request("root", "listTabs")["tabs"]:
            if not re.match(r"^http://(?:127\.0\.0\.1|localhost):8080/(?:[?#]|$)", tab.get("url", "")):
                continue
            frame = self.request(tab["actor"], "getTarget")["frame"]
            actor = frame["consoleActor"]
            state = self.state(actor)
            if state["visibility"] == "visible":
                candidates.append(actor)
        if len(candidates) != 1:
            raise RuntimeError("Exactly one visible local Logos tab is required")
        return candidates[0]

    def state(self, actor):
        return self.evaluate(actor, r"""JSON.stringify((()=>{
          const s=document.getElementById('sidebar');
          const box=e=>e?{x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y,
            width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height,
            right:e.getBoundingClientRect().right}:null;
          const visible=e=>e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0;
          const reachable=id=>{const e=document.getElementById(id);if(!e)return false;
            const r=e.getBoundingClientRect();const x=r.x+r.width/2,y=r.y+r.height/2;
            if(r.width<=0||r.height<=0||x<0||y<0||x>=innerWidth||y>=innerHeight)return false;
            const hit=document.elementFromPoint(x,y);return hit===e||e.contains(hit);};
          return {url:location.origin+location.pathname,title:document.title,
            visibility:document.visibilityState,revision:window.__LOGOS_APP_REVISION,
            open:!!s&&s.classList.contains('open'),sidebar:box(s),
            menu:box(document.getElementById('menu-btn')),
            close:box(document.getElementById('close-sidebar-btn')),
            reachable:{menu:reachable('menu-btn'),newChat:reachable('new-chat-btn'),close:reachable('close-sidebar-btn')},
            expanded:document.getElementById('menu-btn')?.getAttribute('aria-expanded'),
            conversations:document.querySelectorAll('#sidebar .conv-item').length,
            viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio},
            unsafeModal:[...document.querySelectorAll('input[type=password]')].some(visible),
            modalOpen:[...document.querySelectorAll('.modal.visible,#settings-modal-custom,.logos-trace-window,#logos-system-status:not([hidden])')].some(visible)};
        })())""")

    def set_open(self, actor, wanted):
        state = self.state(actor)
        if state["open"] != wanted:
            button = "menu-btn" if wanted else "close-sidebar-btn"
            self.evaluate(actor, "JSON.stringify((()=>{const b=document.getElementById(" +
                          json.dumps(button) + ");if(!b||b.disabled)throw Error('Unavailable button');"
                          "b.click();return {clicked:true};})())")
        for _ in range(30):
            state = self.state(actor)
            box = state["sidebar"]
            settled = box and (abs(box["x"]) < 1 if wanted else box["right"] < 1)
            if state["open"] == wanted and settled:
                time.sleep(0.15)
                return self.state(actor)
            time.sleep(0.1)
        raise RuntimeError("Sidebar did not reach its requested visible state")


def version():
    with urlopen("http://127.0.0.1:8080/api/version", timeout=5) as response:
        if response.geturl() != "http://127.0.0.1:8080/api/version":
            raise RuntimeError("Unexpected version redirect")
        return json.loads(response.read(65536))


def capture(out, uid, gid, metadata):
    device_guard()
    out.mkdir(parents=True, exist_ok=True)
    rdp = RDP()
    actor = rdp.find_logos()
    initial = rdp.state(actor)
    runtime = version()
    if initial["revision"] != runtime.get("runtime_revision"):
        raise RuntimeError("Browser and server revisions differ")
    if initial["title"] != "Logos" or initial["unsafeModal"] or initial["modalOpen"]:
        raise RuntimeError("A clean Logos application view is required")
    files = []
    for filename, wanted in (("01-sidebar-open.png", True),
                             ("02-sidebar-closed.png", False),
                             ("03-sidebar-reopened.png", True)):
        device_guard()
        state = rdp.set_open(actor, wanted)
        if state["revision"] != runtime["runtime_revision"] or state["visibility"] != "visible":
            raise RuntimeError("Application changed during capture")
        if state["unsafeModal"] or state["modalOpen"]:
            raise RuntimeError("An unexpected dialog appeared")
        if not state["reachable"]["newChat" if wanted else "menu"]:
            raise RuntimeError("The requested sidebar control is obscured on screen")
        if state["expanded"] != ("true" if wanted else "false"):
            raise RuntimeError("Sidebar accessibility state differs from its visible state")
        device_guard()
        png = command(ROOT + "screencap", "-p")
        if png[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("Native screenshot is not a PNG")
        width, height = struct.unpack(">II", png[16:24])
        if width != 1080 or height != 2340:
            raise RuntimeError("Unexpected Redmi 9 screen dimensions")
        path = out / filename
        path.write_bytes(png)
        path.chmod(0o600)
        os.chown(path, uid, gid)
        files.append({"path": filename, "sha256": hashlib.sha256(png).hexdigest(),
                      "size": len(png), "width": width, "height": height,
                      "captured_at": datetime.now(timezone.utc).isoformat(), "browser": state})
    rdp.s.close()
    if files[0]["sha256"] == files[1]["sha256"]:
        raise RuntimeError("Open and closed native screenshots are identical")
    manifest = {"schema_version": 1, "device": "Redmi 9", "browser": "Iceraven",
                "capture_method": "Android screencap", "interaction_method": "Firefox DevTools button click",
                "runtime_revision": runtime["runtime_revision"], "ui_version": runtime.get("ui_version"),
                "application_process_id": runtime.get("process_id"),
                "captured_at": files[0]["captured_at"], "initial_sidebar_open": initial["open"],
                "final_sidebar_open": True, "files": files, **metadata}
    path = out / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)
    os.chown(path, uid, gid)
    os.chown(out, uid, gid)
    print(json.dumps({"captured": len(files), "runtime_revision": runtime["runtime_revision"]}))


def main():
    if len(sys.argv) == 6 and sys.argv[1] == "_capture":
        capture(Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), json.loads(sys.argv[5]))
        return
    required = ("GITHUB_ACTIONS", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "RUNNER_NAME", "RUNNER_TEMP", "GITHUB_SHA")
    if os.environ.get("GITHUB_ACTIONS") != "true" or any(not os.environ.get(k) for k in required):
        raise RuntimeError("Run screenshot capture inside the existing GitHub Actions runner")
    if len(sys.argv) != 2:
        raise RuntimeError("Usage: redmi_screenshot_capture.py OUTPUT_DIRECTORY")
    output = Path(sys.argv[1]).resolve()
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    if runner_temp not in output.parents:
        raise RuntimeError("Capture output must be within RUNNER_TEMP")
    metadata = {"github_run_id": os.environ["GITHUB_RUN_ID"],
                "github_run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
                "workflow_revision": os.environ["GITHUB_SHA"], "runner_name": os.environ["RUNNER_NAME"],
                "github_run_url": "https://github.com/maksimp6/Chat/actions/runs/" + os.environ["GITHUB_RUN_ID"]}
    argv = [sys.executable, str(Path(__file__).resolve()), "_capture", str(output),
            str(os.getuid()), str(os.getgid()), json.dumps(metadata)]
    safe_env = {"PATH": os.environ.get("PATH", "/system/bin"), "TMPDIR": os.environ.get("TMPDIR", str(runner_temp))}
    result = subprocess.run(["su", "-c", shlex.join(argv)], env=safe_env, capture_output=True, timeout=90)
    if result.returncode:
        # No job token is in the capture child's environment or argv.
        raise RuntimeError(result.stderr.decode(errors="replace")[-1800:] or "Capture failed")
    print(result.stdout.decode().strip())


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Screenshot capture failed: " + str(error), file=sys.stderr)
        sys.exit(1)
