#!/usr/bin/env python3
"""Capture the real Logos sidebar on the existing Redmi 9 runner."""

import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import shlex
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from urllib.request import urlopen


SOCKET = "\0io.github.forkmaintainers.iceraven/firefox-debugger-socket"
PACKAGE = "io.github.forkmaintainers.iceraven"
ROOT = "/system/bin/"
LOGOS_URL = "http://127.0.0.1:8080/"
# The text model the tools check runs on: the Responses function-calling example in the Yandex docs uses
# yandexgpt, which is YandexGPT Pro 5 in the model picker.
TOOLS_CHECK_MODEL = "yandexgpt-5-pro"
CAPTURE_DEADLINE = None
CAPTURE_STORAGE_ROOT = None
MAX_TEMP_CAPTURE_BYTES = 256 * 1024 * 1024
MIN_CAPTURE_FREE_BYTES = 512 * 1024 * 1024
MAX_VIDEO_BYTES = 20 * 1024 * 1024


def require_ram_disk(path):
    """Refuse to capture unless `path` is on a tmpfs mount: media must never reach flash."""
    path = Path(path).resolve()
    best_target, best_type = "", ""
    with open("/proc/self/mounts", encoding="utf-8") as mounts:
        for line in mounts:
            fields = line.split()
            if len(fields) < 3:
                continue
            target = fields[1].replace("\\040", " ")
            if (path == Path(target) or Path(target) in path.parents) and len(
                target
            ) >= len(best_target):
                best_target, best_type = target, fields[2]
    if best_type != "tmpfs":
        raise CaptureSafetyError(
            "Capture refused: "
            + str(path)
            + " is on "
            + (best_type or "unknown")
            + ", media may only be written to the RAM disk (scripts/redmi_media_ram.sh)"
        )


def capture_storage_guard():
    if CAPTURE_STORAGE_ROOT is None:
        return
    root = CAPTURE_STORAGE_ROOT
    if shutil.disk_usage(root).free < MIN_CAPTURE_FREE_BYTES:
        raise CaptureSafetyError("Capture stopped: less than 512 MiB free")
    total = 0
    # A native runner uses work/<job UUID> for each job. Include every job's
    # capture directories when enforcing the shared temporary-media budget.
    if root.name == "work" and root.parent.name == "github-python-runner":
        workspaces = [
            path for path in root.iterdir() if path.is_dir() and not path.is_symlink()
        ]
    else:
        workspaces = [root]
    for folder in (path for workspace in workspaces for path in workspace.iterdir()):
        if (
            folder.is_symlink()
            or not folder.is_dir()
            or not folder.name.startswith(
                (
                    ".logos-screenshots-",
                    ".logos-tools-check-",
                    ".logos-candidate-tools-",
                    ".logos-screenshot-verification-",
                    ".logos-tools-tests-",
                )
            )
        ):
            continue
        for directory, dirs, names in os.walk(folder, followlinks=False):
            dirs[:] = [name for name in dirs if not Path(directory, name).is_symlink()]
            for name in names:
                path = Path(directory, name)
                if not path.is_symlink() and path.suffix.lower() in {".mp4", ".png"}:
                    total += path.stat().st_size
                    if total >= MAX_TEMP_CAPTURE_BYTES:
                        raise CaptureSafetyError(
                            "Capture stopped: temporary media reached 256 MiB"
                        )


def limit_recording_file():
    # Kernel-enforced boundary also applies while Python waits for browser/model I/O.
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_VIDEO_BYTES, MAX_VIDEO_BYTES))


def capture_url(value):
    """Allow only the installed server or the isolated runner candidate server."""
    if value not in {"http://127.0.0.1:8080/", "http://127.0.0.1:8081/"}:
        raise ValueError("Unsupported local capture URL")
    return value


class CaptureSafetyError(RuntimeError):
    pass


def remaining(limit, deadline=None):
    deadline = deadline if deadline is not None else CAPTURE_DEADLINE
    if deadline is None:
        return limit
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise RuntimeError("The automatic capture time limit was reached")
    return min(limit, seconds)


def command(*args):
    p = subprocess.run(args, capture_output=True, timeout=remaining(20))
    if p.returncode:
        raise RuntimeError("Android command failed: " + Path(args[0]).name)
    return p.stdout


def device_model_guard():
    if command(ROOT + "getprop", "ro.product.model").strip() != b"Redmi 9":
        raise CaptureSafetyError("The selected device is not Redmi 9")


def native_focus_is_logos():
    # The page check cannot see a native window drawn above the WebView, so ask Android who has focus.
    # mCurrentFocus is printed by the full window dump, not by the "windows" subcommand.
    windows = command(ROOT + "dumpsys", "window").decode("utf-8", "replace")
    focus = [line for line in windows.splitlines() if "mCurrentFocus" in line]
    if not focus or PACKAGE not in focus[-1]:
        # Name what has focus, so a failed run shows which window is on top of the application.
        shown = focus[-1].strip()[:300] if focus else "no focused window reported"
        raise CaptureSafetyError(
            "A native window has focus above the application: " + shown
        )


def android_state():
    policy = command(ROOT + "dumpsys", "window", "policy").decode()
    power = command(ROOT + "dumpsys", "power").decode()
    display = command(ROOT + "dumpsys", "display").decode()
    activity = command(ROOT + "dumpsys", "activity", "activities").decode()
    resumed = re.findall(
        r"(?m)^.*(?:mResumedActivity|topResumedActivity):.*$", activity
    )

    def flag(name):
        found = re.search(r"(?m)^\s*" + re.escape(name) + r"=(true|false)\s*$", policy)
        return found.group(1) == "true" if found else None

    def field(pattern, source):
        found = re.search(pattern, source, re.MULTILINE)
        return found.group(1) if found else None

    wakefulness = field(r"^\s*mWakefulness=(Awake|Asleep|Dozing|Dreaming)\s*$", power)
    display_power = field(r"^\s*Display Power:\s*state=([A-Z_]+)\s*$", power)
    global_display = field(r"^\s*mGlobalDisplayState=([A-Z_]+)\s*$", display)
    return {
        "screen_on": display_power == "ON" and global_display == "ON",
        "interactive": wakefulness == "Awake",
        "power_wakefulness": wakefulness,
        "power_display_state": display_power,
        "global_display_state": global_display,
        "window_policy_screen_on": bool(
            re.search(r"(?m)^\s*screenState=SCREEN_STATE_ON\s*$", policy)
        ),
        "window_policy_interactive": bool(
            re.search(r"(?m)^\s*interactiveState=INTERACTIVE_STATE_AWAKE\s*$", policy)
        ),
        "keyguard_showing": flag("showing"),
        "input_restricted": flag("mInputRestricted"),
        "keyguard_secure": flag("secure"),
        "browser_foreground": bool(resumed)
        and all(PACKAGE + "/" in line for line in resumed),
    }


def unlocked_screen(state):
    return (
        state["screen_on"]
        and state["interactive"]
        and state["keyguard_showing"] is False
        and state["input_restricted"] is False
    )


def device_guard():
    capture_storage_guard()
    try:
        return checked_device_guard()
    except CaptureSafetyError:
        raise
    except Exception:
        raise CaptureSafetyError(
            "Android screen and foreground state could not be verified"
        ) from None


def checked_device_guard():
    device_model_guard()
    state = android_state()
    if not state["screen_on"] or not state["interactive"]:
        raise CaptureSafetyError("The Redmi 9 screen is not awake and on")
    if state["keyguard_showing"] is not False or state["input_restricted"] is not False:
        raise CaptureSafetyError("The screen is locked or its state is unknown")
    if not state["browser_foreground"]:
        raise CaptureSafetyError("Iceraven is not the foreground application")


def input_node(expected_name, key_label):
    candidates = []
    for entry in Path("/sys/class/input").glob("event*"):
        if not re.fullmatch(r"event[0-9]+", entry.name):
            continue
        try:
            if (entry / "device/name").read_text().strip() == expected_name:
                candidates.append(Path("/dev/input") / entry.name)
        except OSError:
            continue
    if len(candidates) != 1:
        raise CaptureSafetyError(
            "The expected Redmi 9 kernel input device is unavailable"
        )
    node = candidates[0]
    if not stat.S_ISCHR(node.stat().st_mode):
        raise CaptureSafetyError("The selected input path is not a character device")
    capabilities = command(ROOT + "getevent", "-pl", str(node)).decode(errors="replace")
    if not re.search(r"\b" + re.escape(key_label) + r"\b", capabilities):
        raise CaptureSafetyError(
            "The expected wake key is not supported by its input device"
        )
    return node


def kernel_key_pulse(node, key_code):
    """Atomic evdev equivalent of sendevent, avoiding a held POWER between processes."""
    event = struct.Struct("@llHHI")
    if event.size != 24:
        raise CaptureSafetyError("Unexpected native input-event ABI on Redmi 9")
    sync = event.pack(0, 0, 0, 0, 0)
    release = event.pack(0, 0, 1, key_code, 0) + sync
    packet = event.pack(0, 0, 1, key_code, 1) + sync + release
    fd = os.open(node, os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        if os.write(fd, packet) != len(packet):
            raise CaptureSafetyError("The kernel wake pulse was not fully written")
    finally:
        # Releasing a key must not be skipped because the capture deadline expired.
        try:
            if os.write(fd, release) != len(release):
                raise CaptureSafetyError(
                    "The kernel wake key release was not fully written"
                )
        finally:
            os.close(fd)


def awake_screen(state):
    return state["screen_on"] and state["interactive"]


def strictly_asleep(state):
    return (
        state["power_wakefulness"] == "Asleep"
        and state["power_display_state"] == "OFF"
        and state["global_display_state"] == "OFF"
    )


def wait_awake(seconds):
    deadline = time.monotonic() + seconds
    while True:
        state = android_state()
        if awake_screen(state) or time.monotonic() >= deadline:
            return state
        time.sleep(0.2)


def prepare_browser(metadata):
    """Use the existing insecure screen lock and installed browser; never change either."""
    device_model_guard()
    if not command(ROOT + "pm", "path", PACKAGE).strip().startswith(b"package:"):
        raise RuntimeError("Iceraven is not installed on Redmi 9")
    before = android_state()
    wake_attempts = []
    command(ROOT + "input", "keyevent", "KEYCODE_WAKEUP")
    awake = wait_awake(2)
    wake_attempts.append(
        {"method": "Android KEYCODE_WAKEUP", "awake": awake_screen(awake)}
    )
    if not awake_screen(awake):
        node = input_node("fpc_irq@fingerprint", "KEY_WAKEUP")
        kernel_key_pulse(node, 143)
        awake = wait_awake(4)
        wake_attempts.append(
            {
                "method": "atomic evdev KEY_WAKEUP",
                "device": node.name,
                "device_name": "fpc_irq@fingerprint",
                "awake": awake_screen(awake),
            }
        )
    if not awake_screen(awake):
        node = input_node("mtk-kpd", "KEY_POWER")
        fresh = android_state()
        if not strictly_asleep(fresh):
            raise CaptureSafetyError(
                "Refusing a POWER pulse because Redmi 9 is not confirmed Asleep and OFF"
            )
        kernel_key_pulse(node, 116)
        awake = wait_awake(10)
        wake_attempts.append(
            {
                "method": "single atomic evdev KEY_POWER from confirmed Asleep/OFF",
                "device": node.name,
                "device_name": "mtk-kpd",
                "awake": awake_screen(awake),
            }
        )
    if not awake_screen(awake):
        raise RuntimeError(
            "Redmi 9 did not wake through its validated kernel input devices"
        )
    if awake["keyguard_secure"] is False:
        command(ROOT + "wm", "dismiss-keyguard")
    deadline = time.monotonic() + 8
    while True:
        awake = android_state()
        if unlocked_screen(awake):
            break
        if awake["keyguard_secure"] is not False:
            raise RuntimeError(
                "Redmi 9 requires an existing secure unlock method; automatic dismissal is unavailable"
            )
        command(ROOT + "wm", "dismiss-keyguard")
        if time.monotonic() >= deadline:
            raise RuntimeError("Redmi 9 did not reach an awake, unlocked screen")
        time.sleep(0.3)
    navigation_tag = (
        metadata["github_run_id"]
        + "-"
        + metadata["github_run_attempt"]
        + "-"
        + str(time.time_ns())
    )
    if not re.fullmatch(r"[0-9]+-[0-9]+-[0-9]+", navigation_tag):
        raise RuntimeError("Invalid capture navigation tag")
    fresh_url = LOGOS_URL + "?logos_capture_run=" + navigation_tag
    launch_requested_ms = time.time() * 1000
    launched = command(
        ROOT + "am",
        "start",
        "-W",
        "--user",
        "0",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        fresh_url,
        "-p",
        PACKAGE,
    )
    if b"Error:" in launched or b"Exception" in launched:
        raise RuntimeError("Android could not open the local Logos URL in Iceraven")
    deadline = time.monotonic() + 40
    last_error = "Iceraven has not reached the foreground"
    forced_navigation = False
    while time.monotonic() < deadline:
        rdp = None
        try:
            device_guard()
            rdp = RDP(deadline=deadline)
            actor = rdp.find_logos()
            state = rdp.state(actor)
            fresh = (
                state.get("navigation_tag") == navigation_tag
                and state.get("time_origin_ms", 0) >= launch_requested_ms - 1000
            )
            if not fresh:
                if not forced_navigation:
                    rdp.evaluate(
                        actor,
                        "JSON.stringify((()=>{setTimeout(()=>location.replace("
                        + json.dumps(fresh_url)
                        + "),100);return {scheduled:true};})())",
                    )
                    forced_navigation = True
                raise RuntimeError(
                    "Waiting for a fresh Logos document instead of reusing an old browser tab"
                )
            if (
                state["title"] != "Logos"
                or state["document_ready_state"] != "complete"
                or not state["sidebar"]
                or not state["menu"]
                or not isinstance(state.get("template_revision"), str)
                or not re.fullmatch(r"[0-9a-f]{40}", state["template_revision"])
            ):
                raise RuntimeError("Local Logos has not finished loading")
            rdp.deadline = CAPTURE_DEADLINE
            return (
                rdp,
                actor,
                {
                    "method": "Android/kernel wakeup, insecure keyguard dismissal, browser VIEW intent",
                    "before": before,
                    "wake_attempts": wake_attempts,
                    "ready": android_state(),
                    "navigation": {
                        "tag": navigation_tag,
                        "requested_at_ms": launch_requested_ms,
                        "document_time_origin_ms": state["time_origin_ms"],
                        "rdp_location_replace_used": forced_navigation,
                    },
                },
            )
        except (OSError, RuntimeError, KeyError) as error:
            if rdp is not None:
                rdp.s.close()
            last_error = str(error)
            time.sleep(0.4)
    raise RuntimeError("Automatic browser preparation timed out: " + last_error)


class RDP:
    def __init__(self, deadline=None):
        self.deadline = deadline if deadline is not None else CAPTURE_DEADLINE
        self.s = socket.socket(socket.AF_UNIX)
        try:
            self.s.settimeout(remaining(3, self.deadline))
            self.s.connect(SOCKET)
            self.buffer = b""
            self.receive()
        except Exception:
            self.s.close()
            raise

    def receive(self):
        remaining(3, self.deadline)
        while b":" not in self.buffer:
            self.s.settimeout(remaining(3, self.deadline))
            part = self.s.recv(65536)
            if not part:
                raise RuntimeError("DevTools connection closed")
            self.buffer += part
        size, payload = self.buffer.split(b":", 1)
        size = int(size)
        if size > 4 * 1024 * 1024:
            raise RuntimeError("Unexpected DevTools packet size")
        while len(payload) < size:
            self.s.settimeout(remaining(3, self.deadline))
            part = self.s.recv(65536)
            if not part:
                raise RuntimeError("Truncated DevTools packet")
            payload += part
        self.buffer = payload[size:]
        return json.loads(payload[:size])

    def send(self, data):
        self.s.settimeout(remaining(3, self.deadline))
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
        self.send(
            {"to": actor, "type": "evaluateJSAsync", "text": code, "eager": False}
        )
        for _ in range(200):
            result = self.receive()
            if result.get("type") == "evaluationResult":
                if result.get("hasException"):
                    raise RuntimeError("The browser action failed")
                value = result.get("result")
                if isinstance(value, dict) and value.get("type") == "longString":
                    value = self.read_long_string(value)
                if not isinstance(value, str):
                    raise RuntimeError("Unexpected browser reply")
                return json.loads(value)
            if result.get("error"):
                raise RuntimeError("DevTools evaluation failed")
        raise RuntimeError("Missing browser result")

    def read_long_string(self, grip):
        # Firefox returns large evaluation strings by actor reference. Read the
        # immutable value through the RDP substring API, never truncate a trace.
        actor, length = grip.get("actor"), grip.get("length")
        if (
            not isinstance(actor, str)
            or not actor
            or type(length) is not int
            or length < 0
            or length > 8 * 1024 * 1024
        ):
            raise RuntimeError("Invalid or oversized DevTools long string")
        chunks = []
        for start in range(0, length, 32768):
            end = min(length, start + 32768)
            self.send({"to": actor, "type": "substring", "start": start, "end": end})
            for _ in range(200):
                reply = self.receive()
                if reply.get("from") != actor:
                    continue
                chunk = reply.get("substring")
                if reply.get("error") or not isinstance(chunk, str):
                    raise RuntimeError("DevTools long string read failed")
                if len(chunk.encode("utf-16-le", "surrogatepass")) // 2 != end - start:
                    raise RuntimeError("Incomplete DevTools long string")
                chunks.append(chunk)
                break
            else:
                raise RuntimeError("Missing DevTools long string reply")
        return "".join(chunks).encode("utf-16-le", "surrogatepass").decode("utf-16-le")

    def find_logos(self):
        candidates = []
        for tab in self.request("root", "listTabs")["tabs"]:
            if tab.get("url", "").split("?", 1)[0].split("#", 1)[0] != LOGOS_URL:
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
        return self.evaluate(
            actor,
            r"""JSON.stringify((()=>{
          const s=document.getElementById('sidebar');
          const box=e=>e?{x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y,
            width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height,
            right:e.getBoundingClientRect().right}:null;
          const visible=e=>!!e&&e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0;
          const reachable=id=>{const e=document.getElementById(id);if(!e)return false;
            const r=e.getBoundingClientRect();const x=r.x+r.width/2,y=r.y+r.height/2;
            if(r.width<=0||r.height<=0||x<0||y<0||x>=innerWidth||y>=innerHeight)return false;
            const hit=document.elementFromPoint(x,y);return hit===e||e.contains(hit);};
          const surfaces=[];
          if(visible(document.querySelector('#model-modal.visible')))surfaces.push('model');
          if(visible(document.getElementById('settings-modal-custom')))surfaces.push('settings');
          if(visible(document.querySelector('#logos-system-status:not([hidden])')))surfaces.push('status');
          const statusCounts={};for(const row of document.querySelectorAll('#logos-system-status [data-status]')){
            const value=row.dataset.status;
            const status=['ready','running','waiting','degraded','blocked','revoked','error','stopped'].includes(value)?value:'unknown';
            statusCounts[status]=(statusCounts[status]||0)+1;}
          const runtimeRevision=window.__LOGOS_APP_REVISION;
          const templateRevision=document.getElementById('logos-boot')?.dataset.appRevision;
          const validRevision=value=>typeof value==='string'&&/^[0-9a-f]{40}$/.test(value);
          const revision=validRevision(runtimeRevision)?runtimeRevision:templateRevision;
          const navigation=performance.getEntriesByType('navigation')[0];
          const navigationTag=new URL(navigation?.name||location.href).searchParams.get('logos_capture_run');
          return {url:location.origin+location.pathname,title:document.title,document_ready_state:document.readyState,
            visibility:document.visibilityState,revision,
            template_revision:templateRevision,time_origin_ms:performance.timeOrigin,navigation_tag:navigationTag,
            revision_source:validRevision(runtimeRevision)?'runtime_global':(validRevision(templateRevision)?'server_rendered_script_dataset':'unknown'),
            boot_global_missing:!validRevision(runtimeRevision),core_api_missing:typeof window.LogosCoreAPI==='undefined',
            open:!!s&&s.classList.contains('open'),sidebar:box(s),
            menu:box(document.getElementById('menu-btn')),
            close:box(document.getElementById('close-sidebar-btn')),
            reachable:{menu:reachable('menu-btn'),newChat:reachable('new-chat-btn'),close:reachable('close-sidebar-btn')},
            expanded:document.getElementById('menu-btn')?.getAttribute('aria-expanded'),
            conversations:document.querySelectorAll('#sidebar .conv-item').length,
            viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio},
            surfaces,statusCounts,
            settingsTab:document.querySelector('#settings-modal-custom .llm-tab-btn.active')?.getAttribute('data-tab')||null,
            settingsPanels:[...document.querySelectorAll('#settings-modal-custom .llm-tab-content')].filter(visible).map(e=>e.id),
            unsafeModal:[...document.querySelectorAll('input[type=password]')].some(visible),
            unexpectedModal:[...document.querySelectorAll('.modal.visible')].some(e=>!['model-modal','settings-modal-custom'].includes(e.id)&&visible(e))||
              [...document.querySelectorAll('.logos-trace-window')].some(visible),
            modalOpen:[...document.querySelectorAll('.modal.visible,#settings-modal-custom,.logos-trace-window,#logos-system-status:not([hidden])')].some(visible),
            visibleModals:[...document.querySelectorAll('.modal.visible,.logos-trace-window')].filter(visible).map(e=>
              ((e.id||e.className)+': '+(e.querySelector('.modal-title,h2,h3')?.textContent||'')).trim().slice(0,120))};
        })())""",
        )

    def set_open(self, actor, wanted):
        state = self.state(actor)
        if state["open"] != wanted:
            button = "menu-btn" if wanted else "close-sidebar-btn"
            self.click(actor, "#" + button)
        for _ in range(30):
            state = self.state(actor)
            box = state["sidebar"]
            settled = box and (abs(box["x"]) < 1 if wanted else box["right"] < 1)
            if state["open"] == wanted and settled:
                time.sleep(0.15)
                return self.state(actor)
            time.sleep(0.1)
        raise RuntimeError("Sidebar did not reach its requested visible state")

    def click(self, actor, selector):
        self.evaluate(
            actor,
            "JSON.stringify((()=>{const b=document.querySelector("
            + json.dumps(selector)
            + ");if(!b||b.disabled)throw Error('Unavailable control');"
            "b.scrollIntoView({block:'nearest',inline:'nearest'});"
            "const r=b.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;"
            "const h=document.elementFromPoint(x,y);"
            "if(r.width<=0||r.height<=0||x<0||y<0||x>=innerWidth||y>=innerHeight||"
            "!(h===b||b.contains(h)))throw Error('Control is obscured');"
            "b.click();return {clicked:true};})())",
        )

    def scroll(self, actor, selector, where):
        return self.evaluate(
            actor,
            "JSON.stringify((()=>{const el=document.querySelector("
            + json.dumps(selector)
            + ");if(!el)throw Error('Scroll container missing');"
            "el.scrollTop=" + ("0" if where == "top" else "el.scrollHeight") + ";"
            "return {scrollTop:el.scrollTop,scrollHeight:el.scrollHeight,clientHeight:el.clientHeight};})())",
        )

    def chat_candidates(self, actor):
        return self.evaluate(
            actor,
            "JSON.stringify([...document.querySelectorAll('#conv-list .conv-item')].map((el,i)=>{"
            "const a=el.querySelector('.conv-link');"
            "return {index:i,"
            "id:a?new URL(a.href,location.href).searchParams.get('conversation_id'):null,"
            "title:((el.querySelector('.conv-title')||el).textContent||'').trim(),"
            "active:el.classList.contains('active')};}))",
        )

    def open_conversation(self, actor, index):
        self.evaluate(
            actor,
            "JSON.stringify((()=>{const el=document.querySelectorAll('#conv-list .conv-item')["
            + str(int(index))
            + "];if(!el)throw Error('Conversation missing');el.click();return {opened:true};})())",
        )

    def wait_surface(self, actor, surface, tab=None):
        for _ in range(40):
            state = self.state(actor)
            if state["surfaces"] == ([surface] if surface else []) and (
                tab is None
                or (state["settingsTab"] == tab and state["settingsPanels"] == [tab])
            ):
                time.sleep(0.15)
                return self.state(actor)
            time.sleep(0.1)
        raise RuntimeError(
            "The requested application surface did not become visible: " + str(surface)
        )

    def conversation_state(self, actor):
        return self.evaluate(
            actor,
            r"""JSON.stringify((()=>{
          const ids=typeof conversations!=='undefined'&&Array.isArray(conversations)?
            conversations.map(c=>c&&c.id).filter(id=>typeof id==='string'):[];
          const active=[...document.querySelectorAll('#conv-list .conv-item.active')];
          const activeIds=active.map(row=>{const a=row.querySelector('.conv-link[aria-current="page"]');
            return a?new URL(a.href,location.href).searchParams.get('conversation_id'):null;});
          let stored=null,storageAvailable=true;try{stored=localStorage.getItem('current_conv_id');}
          catch(e){storageAvailable=false;}
          return {current_id:typeof currentConvId==='string'?currentConvId:null,
            current_model:typeof currentModel==='string'?currentModel:null,
            conversation_ids:ids,active_ids:activeIds,active_rows:active.length,
            url_id:new URLSearchParams(location.search).get('conversation_id'),stored_id:stored,storage_available:storageAvailable,
            model_choices:document.querySelectorAll('#model-list > button.model-option.active:not(:disabled)').length,
            message_count:document.querySelectorAll('#chatbox .msg').length,
            empty_state:!!document.querySelector('#chatbox .empty-state'),
            error_state:!!document.querySelector('#chatbox .error-state'),ready_state:document.readyState,
            date_ms:Date.now(),performance_ms:performance.now(),time_origin_ms:performance.timeOrigin};
        })())""",
        )

    def message_counts(self, actor):
        return self.evaluate(
            actor,
            "JSON.stringify({user:document.querySelectorAll('#chatbox .msg.user').length,"
            "bot:document.querySelectorAll('#chatbox .msg.bot').length,"
            "error:document.querySelectorAll('#chatbox .msg.error').length})",
        )

    def send_chat_message(self, actor, text):
        self.evaluate(
            actor,
            "JSON.stringify((()=>{const input=document.getElementById('msg-input');"
            "input.focus();input.value=" + json.dumps(text) + ";"
            "input.dispatchEvent(new Event('input',{bubbles:true}));return {typed:true};})())",
        )
        self.click(actor, "#send-btn")

    def persisted_messages(self, actor, conversation_id):
        return self.evaluate(
            actor,
            "JSON.stringify((()=>{const x=new XMLHttpRequest();"
            "x.open('GET','/api/conversations/'+encodeURIComponent("
            + json.dumps(conversation_id)
            + ")+'/messages',false);x.send();if(x.status!==200)return {status:x.status,messages:[]};"
            "return {status:x.status,messages:(JSON.parse(x.responseText).messages||[]).map(m=>({role:m.role,"
            "text:String(m.text||'').slice(0,10000000),"
            "invocation_id:(m.trace&&m.trace.invocation_id)||null,"
            "errors:((m.trace&&m.trace.errors)||[]).slice(0,10).map(e=>({source:String(e.source||'').slice(0,80),"
            "type:String(e.type||'').slice(0,80),error:String(e.error||'').slice(0,300)}))}))};})())",
        )

    def reply_receipt(self, actor, invocation_id):
        # Billing carries token counts and prices only; tool arguments and results stay out of the evidence.
        return self.evaluate(
            actor,
            "JSON.stringify((()=>{const x=new XMLHttpRequest();"
            "x.open('GET','/api/invocations/'+encodeURIComponent("
            + json.dumps(invocation_id)
            + ")+'/trace',false);x.send();if(x.status!==200)return {http_status:x.status,errors:[]};"
            "const t=JSON.parse(x.responseText),b=t.billing||{};"
            "const pick=(o,keys)=>Object.fromEntries(keys.filter(k=>k in o).map(k=>[k,o[k]]));"
            "return {http_status:x.status,"
            "billing:pick(b,['input_tokens','cached_input_tokens','output_tokens','total_tokens',"
            "'compute_cost','total_cost','cost_status']),"
            "items:(b.items||[]).map(i=>pick(i,['type','step','model','input_tokens','cached_input_tokens',"
            "'output_tokens','total_tokens','total_cost','cost_status'])),"
            "tool_calls:(t.tool_calls||[]).map(c=>c.name||c.tool_name),"
            # Hosted tools (file_search) are sent in the request payload and run by the provider: types and statuses only.
            "sent_tools:[...new Set((t.api_requests||[]).flatMap(r=>(r.payload?.tools||[]).map(i=>String(i.type||''))))],"
            "hosted_calls:(t.responses||[]).flatMap(r=>(r.raw?.output||[]).filter(o=>"
            "['file_search_call','web_search_call','code_interpreter_call'].includes(o.type))"
            ".map(o=>({type:o.type,status:String(o.status||'')}))),"
            "errors:(t.errors||[]).slice(0,50).map(e=>({source:String(e.source||'').slice(0,80),"
            "type:String(e.type||'').slice(0,80),error:String(e.error||'').slice(0,300)})),"
            "events:(t.events||[]).filter(e=>e.type==='api_request_manifest'||e.event==='api_request_manifest')};})())",
        )

    def reply_details(self, actor):
        # The one-line details of the latest reply, as the user reads it: tokens, rubles and seconds.
        return self.evaluate(
            actor,
            "JSON.stringify((()=>{const m=[...document.querySelectorAll('#chatbox .msg.bot')].pop();"
            "const d=m&&m.querySelector('.msg-details');const s=d&&(d.querySelector('summary')||d);"
            "return {text:s?s.textContent.trim():null};})())",
        )

    def tools_inventory(self, actor, conversation_id):
        return self.evaluate(
            actor,
            "JSON.stringify((()=>{const get=p=>{const x=new XMLHttpRequest();"
            "x.open('GET',p,false);x.send();if(x.status!==200)throw Error('Tool inventory unavailable');"
            "return JSON.parse(x.responseText);};return {categories:get('/api/tools/categories').categories,"
            "active_categories:get('/api/conversations/'+encodeURIComponent("
            + json.dumps(conversation_id)
            + ")+'/tools').active_tool_categories};})())",
        )

    def tools_trace(self, actor, invocation_id):
        # Publish only execution outcomes and the existing secret-free request receipt.
        # Tool arguments and results can contain personal files or credentials.
        return self.evaluate(
            actor,
            "JSON.stringify((()=>{const x=new XMLHttpRequest();"
            "x.open('GET','/api/invocations/'+encodeURIComponent("
            + json.dumps(invocation_id)
            + ")+'/trace',false);x.send();if(x.status!==200)return {http_status:x.status};"
            "const t=JSON.parse(x.responseText);const id=v=>typeof v==='string'&&"
            "/^[-A-Za-z0-9_:]{1,180}$/.test(v)&&!/gh[pousr]_|github_pat_|sk-|AQVN|BEGIN/i.test(v)?v:null;"
            "const state=v=>v==null?null:['completed','incomplete','failed','cancelled','queued','in_progress'].includes(v)?v:'UNKNOWN';"
            "const out=r=>(r.output||[]).flatMap(i=>"
            "i.type==='function_call'?[i]:i.type==='message'&&(i.status==null||i.status==='completed')?"
            "(i.content||[]).filter(c=>c.type==='function_call'):[])"
            ".map(i=>({type:'function_call',name:id(i.name),call_id:id(i.call_id),status:state(i.status)}));"
            "return {context:{invocation_id:t.context?.invocation_id},"
            "errors:(t.errors||[]).map(e=>({type:e.type||'unknown'})),"
            "responses:(t.responses||[]).map(r=>({step:r.step,response_id:id(r.response_id),raw:{"
            "id:id(r.raw?.id),status:state(r.raw?.status),incomplete_details:{reason:"
            "['max_output_tokens','content_filter'].includes(r.raw?.incomplete_details?.reason)?"
            "r.raw.incomplete_details.reason:null},"
            "error:r.raw?.error!=null?{present:true}:null,output:out(r.raw||{})}})),"
            "api_requests:(t.api_requests||[]).map(r=>({step:r.step,payload:{tools:(r.payload?.tools||[])"
            ".map(i=>({type:i.type,name:i.name||i.function?.name}))}})),"
            "tool_calls:(t.tool_calls||[]).map(c=>({name:id(c.name||c.tool_name),call_id:id(c.call_id),"
            "step:c.step,error:!!c.error,result:{success:c.result?.success,"
            "metadata:{phase:c.result?.metadata?.phase}},status:c.status})),"
            "events:(t.events||[]).filter(e=>e.type==='api_request_manifest'||e.event==='api_request_manifest')};})())",
        )

    def start_creation_observer(self, actor):
        return self.evaluate(
            actor,
            r"""JSON.stringify((()=>{
          const key='__logosCaptureCreationObserver';if(window[key])throw Error('Capture observer already present');
          const original=window.fetch,requests=[];let counter=0;
          const observer={requests,active:true,expired:false,original,wrapped:null,timer:null,samples:[],sample_overflow:false};
          const stamp=()=>({date_ms:Date.now(),performance_ms:performance.now()});
          let lastSample='';function sample(reason){
            if(!observer.active)return;
            const sidebar=document.getElementById('sidebar');
            const state={current_id:typeof currentConvId==='string'?currentConvId:null,
              conversation_ids:typeof conversations!=='undefined'&&Array.isArray(conversations)?
                conversations.map(c=>c&&c.id).filter(id=>typeof id==='string'):[],
              active_ids:[...document.querySelectorAll('#conv-list .conv-item.active .conv-link[aria-current="page"]')]
                .map(a=>new URL(a.href,location.href).searchParams.get('conversation_id')),
              message_count:document.querySelectorAll('#chatbox .msg').length,
              empty_state:!!document.querySelector('#chatbox .empty-state'),error_state:!!document.querySelector('#chatbox .error-state'),
              sidebar_open:!!sidebar&&sidebar.classList.contains('open'),expanded:document.getElementById('menu-btn')?.getAttribute('aria-expanded')};
            const signature=JSON.stringify(state);if(signature===lastSample)return;lastSample=signature;
            if(observer.samples.length>=256){observer.sample_overflow=true;return;}
            observer.samples.push({...state,...stamp(),reason});}
          observer.mutations=new MutationObserver(()=>sample('mutation'));
          observer.mutations.observe(document.body,{subtree:true,childList:true,attributes:true,
            attributeFilter:['class','aria-expanded','aria-current','href']});sample('initial');
          function wrapped(...args){
            let record=null;try{const raw=args[0],u=new URL(typeof raw==='string'||raw instanceof URL?String(raw):raw.url,location.href);
              if(observer.active&&u.origin===location.origin&&(/^\/api\/conversations(?:\/|$)/.test(u.pathname)||u.pathname==='/api/chat')){
                const method=String(args[1]?.method||raw?.method||'GET').toUpperCase();
                record={id:++counter,path:u.pathname.slice(0,256),method:/^[A-Z]{3,8}$/.test(method)?method:'OTHER',
                  started:stamp(),finished:null,status:null,error:null,body_done:false,
                  response_id:null,response_ids:null,response_message_count:null,blocked:false};requests.push(record);}}
            catch(e){}
            if(record&&record.path==='/api/chat'){
              record.blocked=true;record.finished=stamp();record.body_done=true;record.error='generation_blocked_by_capture';
              return Promise.reject(new Error('Message generation is disabled during this free capture'));}
            let promise;try{promise=Reflect.apply(original,this,args);}catch(e){
              if(record){record.finished=stamp();record.error='fetch_threw';record.body_done=true;}throw e;}
            if(record)promise.then(response=>{record.finished=stamp();record.status=response.status;
              if(!response.ok){record.body_done=true;return;}
              response.clone().json().then(data=>{
                if(record.path==='/api/conversations'&&record.method==='POST')
                  record.response_id=typeof data?.id==='string'?data.id:null;
                if(record.path==='/api/conversations'&&record.method==='GET'){
                  const list=Array.isArray(data)?data:data?.conversations;
                  record.response_ids=Array.isArray(list)?list.map(c=>c?.id).filter(id=>typeof id==='string'):null;}
                if(/\/api\/conversations\/[^/]+\/messages$/.test(record.path)){
                  const messages=Array.isArray(data)?data:data?.messages;
                  record.response_message_count=Array.isArray(messages)?messages.length:null;}
                record.body_done=true;sample('response_metadata');
              },()=>{record.body_done=true;record.error='response_metadata_unavailable';});
            },()=>{record.finished=stamp();record.error='fetch_rejected';record.body_done=true;});
            return promise;
          }
          observer.wrapped=wrapped;window.fetch=wrapped;window[key]=observer;
          observer.timer=setTimeout(()=>{if(window.fetch===wrapped)window.fetch=original;
            observer.active=false;observer.mutations.disconnect();observer.expired=true;},60000);
          return {installed:window.fetch===wrapped,date_ms:Date.now(),performance_ms:performance.now(),time_origin_ms:performance.timeOrigin};
        })())""",
        )

    def creation_observer(self, actor, stop=False):
        return self.evaluate(
            actor,
            "JSON.stringify((()=>{const key='__logosCaptureCreationObserver',o=window[key];"
            "if(!o)return {available:false,requests:[]};"
            "const result={available:true,intact:window.fetch===o.wrapped,expired:o.expired,requests:o.requests,"
            "samples:o.samples,sample_overflow:o.sample_overflow};"
            + (
                "o.active=false;if(window.fetch===o.wrapped)window.fetch=o.original;o.mutations.disconnect();clearTimeout(o.timer);delete window[key];"
                if stop
                else ""
            )
            + "return result;})())",
        )

    def request_creation_reads(self, actor, conversation_id):
        self.evaluate(
            actor,
            "JSON.stringify((()=>{for(const path of ['/api/conversations',"
            "'/api/conversations/'+encodeURIComponent("
            + json.dumps(conversation_id)
            + ")+ '/messages'])"
            "fetch(path,{cache:'no-store'}).catch(()=>{});return {requested:true};})())",
        )


def version():
    with urlopen(LOGOS_URL + "api/version", timeout=5) as response:
        if response.geturl() != LOGOS_URL + "api/version":
            raise RuntimeError("Unexpected version redirect")
        return json.loads(response.read(65536))


DIALOGS_VIDEO_NAME = "16-dialogs-walk.mp4"


def start_screenrecord(video_path, time_limit, bit_rate):
    """Start the device recorder, or return None when the device has no screenrecord binary."""
    capture_storage_guard()
    if not Path(ROOT + "screenrecord").is_file():
        return None
    return subprocess.Popen(
        [
            ROOT + "screenrecord",
            "--bugreport",
            "--size",
            "720x1560",
            "--bit-rate",
            str(bit_rate),
            "--time-limit",
            str(time_limit),
            str(video_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=limit_recording_file,
    )


def finish_screenrecord(recorder, video_path, uid, gid, max_bytes, *, event_sink=None):
    """Stop a device recording; keep it only when its MP4 container is complete."""
    failures = []
    video = {"path": video_path.name, "available": False}
    artifact = None
    if event_sink is not None:
        event_sink("recorder_stop_requested")
    if recorder.poll() is None:
        try:
            recorder.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass
    try:
        recorder.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            recorder.kill()
        except ProcessLookupError:
            pass
        recorder.wait(timeout=3)
        failures.append("native_screenrecord_stop_failed")
    if event_sink is not None:
        event_sink("recorder_stop_observed")
    video["exit_code"] = recorder.returncode
    if video_path.is_file() and 16 <= video_path.stat().st_size <= max_bytes:
        from publish_redmi_screenshots import validate_mp4

        try:
            validate_mp4(video_path)
        except Exception:
            pass
        else:
            content = video_path.read_bytes()
            video["available"] = True
            video_path.chmod(0o600)
            os.chown(video_path, uid, gid)
            artifact = {
                "path": video_path.name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
    if not video["available"]:
        failures.append("native_video_artifact_unavailable")
    return video, artifact, failures


# Runs 40699209617 and 40699817371: without a stated query the model asked for permission,
# then for keywords, instead of searching.
LOGOS_RAG_PROMPT = (
    "Разрешение уже есть, ничего не уточняй: сразу выполни поиск File Search (RAG) "
    "по запросам «Логос», «Судный Логос» и «КРОВЬ, ВОПИЮЩАЯ ОТ ЗЕМЛИ» и прочитай найденные "
    "диалоги с Логосом. Потом ответь на вопрос: можешь ли ты судить как Логос?"
)


def tools_self_check(rdp, actor, conversation_id):
    """Ask Logos through the actual Send button; judge calls independently of its claims."""
    from logos_tools_self_check import build_self_check_question, evaluate_tool_evidence

    inventory = rdp.tools_inventory(actor, conversation_id)
    before = rdp.persisted_messages(actor, conversation_id)
    if before["status"] != 200:
        return {"status": "ERROR", "reason": "baseline_messages_unavailable"}
    if rdp.conversation_state(actor)["current_id"] != conversation_id:
        return {"status": "ERROR", "reason": "requested_chat_not_selected"}
    # The question is a text request with tool calls. An image model refuses it (its prompt limit is
    # 1000 characters), so the chat is moved to the text model whose documented example calls functions.
    if rdp.conversation_state(actor)["current_model"] != TOOLS_CHECK_MODEL:
        rdp.evaluate(
            actor,
            "JSON.stringify((()=>{window.changeModel("
            + json.dumps(TOOLS_CHECK_MODEL)
            + ", false);return {changed:true};})())",
        )
        for _ in range(30):
            device_guard()
            if rdp.conversation_state(actor)["current_model"] == TOOLS_CHECK_MODEL:
                break
            time.sleep(0.2)
        if rdp.conversation_state(actor)["current_model"] != TOOLS_CHECK_MODEL:
            return {"status": "ERROR", "reason": "tools_check_model_not_selected"}
    # #msg-input is input[type=text]; it removes line breaks before sending.
    nonce = uuid.uuid4().hex
    folder = "tmp/logos-tools-check-" + nonce
    candidate_root = (
        str(Path(__file__).resolve().parents[1])
        if LOGOS_URL.endswith(":8081/")
        else None
    )
    question = build_self_check_question(
        inventory, nonce, folder, project_root=candidate_root
    )
    rdp.send_chat_message(actor, question)
    # Leave time to finalize the recording and publish even when the app times out.
    deadline = time.monotonic() + max(0, remaining(120) - 15)
    after, traces = before, {}
    while time.monotonic() < deadline:
        device_guard()
        after = rdp.persisted_messages(actor, conversation_id)
        if after["status"] != 200:
            break
        fresh = after["messages"][len(before["messages"]) :]
        question_positions = [
            i
            for i, m in enumerate(fresh)
            if m["role"] == "user" and m["text"] == question
        ]
        old_invocations = {
            m["invocation_id"] for m in before["messages"] if m["invocation_id"]
        }
        replies = fresh[question_positions[-1] + 1 :] if question_positions else []
        if any(
            m["role"] == "assistant"
            and m["text"].strip()
            and m["invocation_id"]
            and m["invocation_id"] not in old_invocations
            for m in replies
        ):
            for m in replies:
                if (
                    m["role"] == "assistant"
                    and m["invocation_id"]
                    and m["invocation_id"] not in old_invocations
                ):
                    traces[m["invocation_id"]] = rdp.tools_trace(
                        actor, m["invocation_id"]
                    )
            break
        time.sleep(1)
    report = evaluate_tool_evidence(
        inventory["categories"],
        inventory["active_categories"],
        before["messages"],
        after["messages"],
        traces,
        question,
    )
    report["question"] = question
    report["file_fixture"] = {
        "nonce": nonce,
        "folder": folder,
        "extended": candidate_root is not None,
    }
    report["execution_traces"] = traces
    report["browser_observation"] = rdp.evaluate(
        actor,
        "JSON.stringify({"
        "tool_summaries:[...document.querySelectorAll('.msg-technical-summary')]"
        ".map(e=>e.textContent),zero_calls_visible:[...document.querySelectorAll('.msg-technical-label')]"
        ".some(e=>e.textContent.includes('Вызовы инструментов: 0')),"
        "error_messages:document.querySelectorAll('#chatbox .msg.error').length})",
    )
    # The original reply remains in the app conversation. Do not publish free text from
    # a model with filesystem tools; the evidence report contains only structured facts.
    return report


def record_creation(rdp, actor, out, uid, gid, snapshot, tools_check=False):
    """Observe one real new-chat flow, preserving application failures as evidence."""
    events, failures, artifacts = [], [], []
    recorder = None
    video_path = out / "15-conversation-create.mp4"
    video_seconds = 180
    video_deadline = None
    video = {
        "path": video_path.name,
        "available": False,
        "time_limit_seconds": video_seconds,
        "clock_note": "Process timestamps bound recording; they do not identify the exact first video frame.",
    }
    observer = {"available": False, "requests": []}
    reload_observer = {"available": False, "requests": []}
    checkpoints = {
        "new_conversation_postconditions": {
            "status": "NOT_RUN",
            "reason": "creation_not_confirmed",
        },
        "reload_persistence": {"status": "NOT_RUN", "reason": "creation_not_confirmed"},
        "message_send": {"status": "NOT_RUN", "reason": "creation_not_confirmed"},
    }
    if tools_check:
        checkpoints["tools_self_check"] = {
            "status": "NOT_RUN",
            "reason": "new_chat_not_ready",
        }
    else:
        checkpoints["logos_rag_judgement"] = {
            "status": "NOT_RUN",
            "reason": "message_not_answered",
        }
    before = after = reloaded = None

    def mark(name, browser=None):
        event = {
            "event": name,
            "wall_utc": datetime.now(timezone.utc).isoformat(),
            "monotonic_ns": time.monotonic_ns(),
        }
        if browser is not None:
            event["browser"] = browser
        if recorder is not None:
            event["recorder_running"] = recorder.poll() is None
        if name in (
            "new_chat_button_clicked",
            "current_model_choice_started",
            "current_model_choice_clicked",
            "creation_result_observed",
            "reload_result_observed",
            "native_screencap_finished:14-conversation-after-reload.png",
            "message_send_clicked",
            "message_reply_observed",
            "screenshot_finished:22-logos-rag-top.png",
            "screenshot_finished:23-logos-rag-bottom.png",
        ):
            if not event.get("recorder_running"):
                failures.append("native_video_ended_before_scenario_complete")
        events.append(event)

    def app_state():
        device_guard()
        state = rdp.state(actor)
        if (
            state["visibility"] != "visible"
            or state["title"] != "Logos"
            or state["url"] != LOGOS_URL
            or state["unsafeModal"]
            or state["unexpectedModal"]
            or state["surfaces"] not in ([], ["model"])
        ):
            raise CaptureSafetyError(
                "The conversation recording left the clean local Logos view"
            )
        return state

    def frame(filename):
        state = app_state()
        mark("screenshot_started:" + filename)
        snapshot(
            filename,
            surface="model" if state["surfaces"] else None,
            capture_actor=actor,
            event_sink=mark,
        )
        mark("screenshot_finished:" + filename)

    def check_created(baseline, result, new_id, phase):
        if not new_id or new_id == baseline["current_id"]:
            failures.append("new_conversation_id_missing_" + phase)
            return
        if (
            result["current_id"] != new_id
            or result["url_id"] != new_id
            or result["stored_id"] != new_id
            or result["active_ids"] != [new_id]
            or result["active_rows"] != 1
        ):
            failures.append("conversation_selection_incoherent_" + phase)
        expected = set(baseline["conversation_ids"]) | {new_id}
        actual = result["conversation_ids"]
        if (
            set(actual) != expected
            or len(actual) != len(set(actual))
            or new_id in baseline["conversation_ids"]
        ):
            failures.append("conversation_list_incoherent_" + phase)
        if result["current_model"] != baseline["current_model"]:
            failures.append("current_model_changed_" + phase)
        if (
            result["message_count"] != 0
            or not result["empty_state"]
            or result["error_state"]
        ):
            failures.append("new_conversation_not_empty_" + phase)

    try:
        initial_view = app_state()
        if initial_view["boot_global_missing"]:
            failures.append("document_boot_global_missing")
        if initial_view["core_api_missing"]:
            failures.append("document_core_api_missing")
        before = rdp.conversation_state(actor)
        mark("scenario_started", before)
        if not Path(ROOT + "screenrecord").is_file():
            failures.append("native_screenrecord_unavailable")
        else:
            mark("recorder_launch_requested")
            recorder = start_screenrecord(video_path, video_seconds, 800000)
            video_deadline = time.monotonic() + video_seconds
            time.sleep(0.5)
            mark("recorder_start_observed")
            if recorder.poll() is not None:
                failures.append("native_screenrecord_start_failed")
        frame("11-conversation-before.png")
        installed = rdp.start_creation_observer(actor)
        mark("network_observer_installed", installed)
        mark("new_chat_button_attempted", rdp.conversation_state(actor))
        rdp.click(actor, "#new-chat-btn")
        mark("new_chat_button_clicked", rdp.conversation_state(actor))
        try:
            rdp.wait_surface(actor, "model")
        except CaptureSafetyError:
            raise
        except (OSError, RuntimeError, KeyError):
            failures.append("new_chat_model_picker_did_not_open")
        frame("12-conversation-model-picker.png")
        picker_open = app_state()["surfaces"] == ["model"]
        if not picker_open:
            failures.append("new_chat_model_picker_did_not_open")
        choice = rdp.conversation_state(actor)
        picker_observer = rdp.creation_observer(actor)
        premature_posts = [
            r for r in picker_observer["requests"] if r["method"] == "POST"
        ]
        if premature_posts:
            failures.append("creation_request_before_model_choice")
        if (
            choice["current_id"] != before["current_id"]
            or choice["conversation_ids"] != before["conversation_ids"]
            or choice["active_ids"] != before["active_ids"]
        ):
            failures.append("conversation_changed_before_model_choice")
        mark(
            "model_picker_phase_observed",
            {"conversation": choice, "network": picker_observer},
        )
        if (
            not picker_open
            or premature_posts
            or choice["model_choices"] != 1
            or choice["current_model"] != before["current_model"]
        ):
            failures.append("same_current_model_choice_unavailable")
        else:
            mark("current_model_choice_started", choice)
            rdp.click(actor, "#model-list > button.model-option.active:not(:disabled)")
            mark("current_model_choice_clicked")
            deadline = time.monotonic() + 15
            settled_since = None
            while time.monotonic() < deadline:
                app_state()
                observer = rdp.creation_observer(actor)
                after = rdp.conversation_state(actor)
                posts = [
                    r
                    for r in observer["requests"]
                    if r["path"] == "/api/conversations" and r["method"] == "POST"
                ]
                finished = posts and all(r["finished"] is not None for r in posts)
                quiet = finished and all(
                    r["finished"] is not None and r["body_done"]
                    for r in observer["requests"]
                )
                if quiet:
                    settled_since = (
                        settled_since if settled_since is not None else time.monotonic()
                    )
                    if time.monotonic() - settled_since >= 0.5:
                        break
                else:
                    settled_since = None
                time.sleep(0.2)
            mark("creation_result_observed", after)
            observer = rdp.creation_observer(actor, stop=True)
            posts = [
                r
                for r in observer["requests"]
                if r["path"] == "/api/conversations" and r["method"] == "POST"
            ]
            if (
                not observer.get("available")
                or not observer.get("intact")
                or observer.get("expired")
                or not posts
            ):
                failures.append("creation_network_observer_unavailable")
            elif len(posts) != 1:
                failures.append("creation_request_count_not_one")
            elif posts[0]["status"] != 201:
                failures.append("creation_request_did_not_return_201")
            elif posts[0]["error"] is not None or not posts[0]["body_done"]:
                failures.append("creation_response_metadata_unavailable")
            if any(r["path"] == "/api/chat" for r in observer["requests"]):
                failures.append("unexpected_message_generation_request")
            post = posts[0] if len(posts) == 1 else None
            confirmed_response = (
                post is not None
                and post["status"] == 201
                and post["error"] is None
                and post["body_done"]
            )
            response_id = post["response_id"] if confirmed_response else None
            new_id = (
                response_id
                if (
                    isinstance(response_id, str)
                    and response_id
                    and response_id != before["current_id"]
                    and response_id not in before["conversation_ids"]
                )
                else None
            )
            if confirmed_response and new_id is None:
                failures.append("creation_response_id_not_new")
            rejected = (
                post is not None
                and post["finished"] is not None
                and isinstance(post["status"], int)
                and 400 <= post["status"] <= 599
            )
            not_run_reason = (
                "creation_request_rejected" if rejected else "creation_not_confirmed"
            )
            if rejected:
                preserved_fields = (
                    "current_id",
                    "current_model",
                    "conversation_ids",
                    "active_ids",
                    "active_rows",
                    "url_id",
                    "stored_id",
                    "message_count",
                    "empty_state",
                    "error_state",
                )
                changed = [key for key in preserved_fields if after[key] != before[key]]
                checkpoints["existing_conversation_view_after_rejection"] = {
                    "status": "FAIL" if changed else "PASS",
                    "changed_fields": changed,
                    "http_status": post["status"],
                }
                if changed:
                    failures.append(
                        "rejected_creation_changed_existing_conversation_view"
                    )
            checkpoints["creation_response"] = {
                "status": "PASS" if new_id else "FAIL",
                "http_status": post["status"] if post else None,
                "confirmed_new_id": new_id,
            }
            if any(r["method"] == "PATCH" for r in observer["requests"]):
                failures.append("existing_conversation_patched_during_creation")
            if observer.get("sample_overflow") or not observer.get("samples"):
                failures.append("conversation_transition_observer_unavailable")
            state = app_state()
            if new_id:
                validation_start = len(failures)
                if after["current_id"] != new_id:
                    failures.append("created_response_id_differs_from_selected_id")
                message_reads = [
                    r
                    for r in observer["requests"]
                    if r["path"] == "/api/conversations/" + new_id + "/messages"
                    and r["method"] == "GET"
                ]
                if not message_reads or any(
                    r["status"] != 200
                    or not r["body_done"]
                    or r["response_message_count"] != 0
                    for r in message_reads
                ):
                    failures.append("new_conversation_empty_history_not_verified")
                seen_new = False
                for sample in observer.get("samples", []):
                    if sample["current_id"] == new_id:
                        seen_new = True
                        if sample["message_count"]:
                            failures.append(
                                "old_messages_visible_under_new_conversation"
                            )
                    elif seen_new:
                        failures.append("new_conversation_selection_reverted")
                check_created(before, after, new_id, "after_create")
                if state["surfaces"] or state["open"] or not state["reachable"]["menu"]:
                    failures.append("created_chat_view_obscured_after_create")
                if state["expanded"] != ("true" if state["open"] else "false"):
                    failures.append("sidebar_aria_mismatch_after_create")
                checkpoints["new_conversation_postconditions"] = {
                    "status": "FAIL" if len(failures) > validation_start else "PASS"
                }
            else:
                checkpoints["new_conversation_postconditions"] = {
                    "status": "NOT_RUN",
                    "reason": not_run_reason,
                }
            mark(
                "sidebar_after_creation",
                {"open": state["open"], "expanded": state["expanded"]},
            )
            frame("13-conversation-created.png")
            if new_id:
                reload_validation_start = len(failures)
                origin_before = after["time_origin_ms"]
                mark("reload_requested", rdp.conversation_state(actor))
                rdp.evaluate(
                    actor,
                    "JSON.stringify((()=>{setTimeout(()=>location.reload(),100);return {scheduled:true};})())",
                )
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    device_guard()
                    try:
                        actor = rdp.find_logos()
                        app_state()
                        candidate = rdp.conversation_state(actor)
                        if (
                            candidate["time_origin_ms"] != origin_before
                            and candidate["ready_state"] == "complete"
                        ):
                            reloaded = candidate
                            if (
                                candidate["current_id"] == new_id
                                and candidate["active_ids"] == [new_id]
                                and candidate["empty_state"]
                                and not candidate["error_state"]
                            ):
                                break
                    except CaptureSafetyError:
                        raise
                    except (OSError, RuntimeError, KeyError):
                        pass
                    time.sleep(0.2)
                mark("reload_result_observed", reloaded)
                if reloaded is None:
                    failures.append("conversation_reload_not_observed")
                else:
                    if reloaded["current_id"] != new_id:
                        failures.append("new_conversation_not_selected_after_reload")
                    check_created(before, reloaded, new_id, "after_reload")
                    state = app_state()
                    if (
                        state["surfaces"]
                        or state["open"]
                        or not state["reachable"]["menu"]
                    ):
                        failures.append("created_chat_view_obscured_after_reload")
                    rdp.start_creation_observer(actor)
                    mark("reload_server_reads_requested", reloaded)
                    rdp.request_creation_reads(actor, new_id)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        app_state()
                        reload_observer = rdp.creation_observer(actor)
                        records = reload_observer["requests"]
                        if len(records) >= 2 and all(
                            r["finished"] is not None and r["body_done"]
                            for r in records
                        ):
                            break
                        time.sleep(0.2)
                    reload_observer = rdp.creation_observer(actor, stop=True)
                    server_lists = [
                        r
                        for r in reload_observer["requests"]
                        if r["path"] == "/api/conversations" and r["method"] == "GET"
                    ]
                    server_messages = [
                        r
                        for r in reload_observer["requests"]
                        if r["path"] == "/api/conversations/" + new_id + "/messages"
                        and r["method"] == "GET"
                    ]
                    expected_server_ids = set(before["conversation_ids"]) | {new_id}
                    if not server_lists or any(
                        r["status"] != 200
                        or not r["body_done"]
                        or r["response_ids"] is None
                        or set(r["response_ids"]) != expected_server_ids
                        or len(r["response_ids"]) != len(set(r["response_ids"]))
                        for r in server_lists
                    ):
                        failures.append(
                            "new_conversation_not_verified_in_server_list_after_reload"
                        )
                    if not server_messages or any(
                        r["status"] != 200
                        or not r["body_done"]
                        or r["response_message_count"] != 0
                        for r in server_messages
                    ):
                        failures.append(
                            "empty_history_not_verified_on_server_after_reload"
                        )
                    mark("reload_server_reads_observed", reload_observer)
                    if (
                        not reload_observer.get("available")
                        or not reload_observer.get("intact")
                        or reload_observer.get("expired")
                        or reload_observer.get("sample_overflow")
                    ):
                        failures.append("reload_network_observer_unavailable")
                    if any(
                        r["path"] == "/api/chat" or r["method"] != "GET"
                        for r in reload_observer["requests"]
                    ):
                        failures.append("unexpected_mutating_request_after_reload")
                    reloaded = rdp.conversation_state(actor)
                    if reloaded["current_id"] != new_id:
                        failures.append("new_conversation_not_selected_after_reload")
                    check_created(before, reloaded, new_id, "after_reload")
                    state = app_state()
                    if (
                        state["surfaces"]
                        or state["open"]
                        or not state["reachable"]["menu"]
                    ):
                        failures.append("created_chat_view_obscured_after_reload")
                    if state["expanded"] != ("true" if state["open"] else "false"):
                        failures.append("sidebar_aria_mismatch_after_reload")
                    frame("14-conversation-after-reload.png")
                checkpoints["reload_persistence"] = {
                    "status": "FAIL"
                    if len(failures) > reload_validation_start
                    else "PASS"
                }
            else:
                checkpoints["reload_persistence"] = {
                    "status": "NOT_RUN",
                    "reason": not_run_reason,
                }
            if checkpoints["reload_persistence"]["status"] == "PASS" and tools_check:
                # The requested self-check is the only model request in this mode.
                mark("tools_self_check_requested")
                checkpoints["tools_self_check"] = {
                    "status": "ERROR",
                    "reason": "check_interrupted",
                }
                checkpoints["tools_self_check"] = tools_self_check(rdp, actor, new_id)
                mark("tools_self_check_observed", checkpoints["tools_self_check"])
                if checkpoints["tools_self_check"]["status"] != "PASS":
                    failures.append("tools_self_check_incomplete_or_failed")
                if recorder is None or recorder.poll() is not None:
                    failures.append("native_video_ended_before_tools_check_complete")
                frame("22-tools-self-check.png")
            elif checkpoints["reload_persistence"]["status"] == "PASS":
                send_validation_start = len(failures)
                if recorder is None or recorder.poll() is not None:
                    raise RuntimeError(
                        "The Logos RAG scenario requires an active native recording"
                    )
                reply_budget = min(
                    120, remaining(135) - 15, video_deadline - time.monotonic() - 15
                )
                if reply_budget <= 0:
                    raise RuntimeError(
                        "No recording time remains for the Logos RAG scenario"
                    )
                counts_before = rdp.message_counts(actor)
                rdp.send_chat_message(actor, LOGOS_RAG_PROMPT)
                mark("message_send_clicked", rdp.conversation_state(actor))
                reply_status = "timeout"
                deadline = time.monotonic() + reply_budget
                while time.monotonic() < deadline:
                    device_guard()
                    counts = rdp.message_counts(actor)
                    if counts["error"] > counts_before["error"]:
                        reply_status = "error"
                        break
                    if counts["bot"] > counts_before["bot"]:
                        reply_status = "answered"
                        break
                    time.sleep(1)
                mark("message_reply_observed", {"reply_status": reply_status})
                if reply_status != "answered":
                    failures.append("message_reply_" + reply_status)
                details = (
                    rdp.reply_details(actor)
                    if reply_status == "answered"
                    else {"text": None}
                )
                persisted = rdp.persisted_messages(actor, new_id)
                roles = [m["role"] for m in persisted["messages"]]
                replies = [
                    m["text"] for m in persisted["messages"] if m["role"] == "assistant"
                ]
                assistant_messages = [
                    m for m in persisted["messages"] if m["role"] == "assistant"
                ]
                reply_errors = [e for m in assistant_messages for e in m["errors"]]
                receipt = None
                for m in assistant_messages:
                    # A reply without an invocation has no trace to read, so «no trace errors» would prove nothing.
                    if not m["invocation_id"]:
                        failures.append("reply_invocation_missing")
                        continue
                    receipt = {
                        "invocation_id": m["invocation_id"],
                        **rdp.reply_receipt(actor, m["invocation_id"]),
                    }
                    if receipt["http_status"] != 200:
                        failures.append("reply_trace_unavailable")
                    reply_errors.extend(receipt["errors"])
                # The details line must show the turn the trace billed: every model call's tokens and the calculated cost.
                details_check = {
                    "status": "NOT_RUN",
                    "reason": "reply_not_answered"
                    if reply_status != "answered"
                    else "reply_trace_unavailable",
                }
                if (
                    reply_status == "answered"
                    and receipt
                    and receipt["http_status"] == 200
                ):
                    billing = receipt["billing"]
                    # A reply without a details line shows no tokens and no cost, which is compared like any other.
                    shown = details["text"] or ""
                    shown_tokens = re.search(r"(\d+) токенов", shown)
                    shown_rub = re.search(r"≈ (\d+(?:\.\d+)?) ₽", shown)
                    # The line leaves out a zero count and a zero or uncalculated cost, so those are expected absent.
                    expected_tokens = billing.get("total_tokens") or None
                    expected_rub = (
                        billing.get("total_cost")
                        if billing.get("cost_status") == "calculated"
                        else None
                    )
                    expected_rub = expected_rub or None
                    shown_count = int(shown_tokens.group(1)) if shown_tokens else None
                    shown_cost = float(shown_rub.group(1)) if shown_rub else None
                    # Without billed model calls the line shows the provider's usage, which the trace does not total.
                    tokens_match = (
                        not receipt["items"] or shown_count == expected_tokens
                    )
                    rub_match = (
                        shown_cost is None
                        if expected_rub is None
                        else shown_cost is not None
                        and abs(shown_cost - expected_rub) <= 0.005 + 1e-9
                    )
                    details_check = {
                        "status": "PASS" if tokens_match and rub_match else "FAIL",
                        "shown_tokens": shown_count,
                        "trace_total_tokens": expected_tokens,
                        "shown_rub": shown_cost,
                        "trace_total_cost": expected_rub,
                    }
                    if details_check["status"] == "FAIL":
                        failures.append("reply_details_mismatch_trace")
                prompt_persisted = any(
                    m["role"] == "user" and m["text"] == LOGOS_RAG_PROMPT
                    for m in persisted["messages"]
                )
                if persisted["status"] != 200 or not prompt_persisted:
                    failures.append("user_message_not_persisted")
                if "assistant" not in roles:
                    failures.append("reply_not_persisted")
                if reply_errors:
                    failures.append("reply_trace_errors")
                checkpoints["message_send"] = {
                    "status": "FAIL"
                    if len(failures) > send_validation_start
                    else "PASS",
                    "reply_status": reply_status,
                    "reply_text": replies[-1] if replies else None,
                    "reply_trace_errors": reply_errors,
                    "reply_receipt": receipt,
                    "browser_details": details["text"],
                    "details_vs_trace": details_check,
                    "question": LOGOS_RAG_PROMPT,
                    "user_message_persisted": prompt_persisted,
                    "reply_persisted": "assistant" in roles,
                }
                if checkpoints["message_send"]["status"] == "PASS":
                    reply_text = replies[-1] if replies else ""
                    # The trace shows whether File Search was offered to the model and whether the provider ran it.
                    sent_tools = (receipt or {}).get("sent_tools") or []
                    hosted_calls = (receipt or {}).get("hosted_calls") or []
                    rag_sent = "file_search" in sent_tools
                    rag_called = any(
                        call["type"] == "file_search_call" for call in hosted_calls
                    )
                    answered = bool(re.search(r"\w", reply_text))
                    reasons = [
                        reason
                        for reason, ok in (
                            ("rag_not_sent", rag_sent),
                            ("rag_not_called", rag_called),
                            ("empty_answer", answered),
                        )
                        if not ok
                    ]
                    checkpoints["logos_rag_judgement"] = {
                        "status": "FAIL" if reasons else "PASS",
                        "reasons": reasons,
                        "file_search_sent": rag_sent,
                        "file_search_called": rag_called,
                        "sent_tools": sent_tools,
                        "hosted_calls": hosted_calls,
                        "answered": answered,
                        "scope": "RAG read and an answer exist; the judgement itself is not graded",
                    }
                    failures.extend("logos_rag_" + reason for reason in reasons)
                    mark(
                        "logos_rag_judgement_observed",
                        checkpoints["logos_rag_judgement"],
                    )
                    for where, filename in (
                        ("top", "22-logos-rag-top.png"),
                        ("bottom", "23-logos-rag-bottom.png"),
                    ):
                        app_state()
                        mark(
                            "logos_rag_scrolled",
                            {"where": where, **rdp.scroll(actor, "#chatbox", where)},
                        )
                        frame(filename)
            else:
                checkpoints["message_send"] = {
                    "status": "NOT_RUN",
                    "reason": "creation_not_confirmed",
                }
    except CaptureSafetyError:
        raise
    except Exception as error:
        failures.append("conversation_observation_failed")
        mark("observation_error", {"exception_type": type(error).__name__})
        try:
            frame("13-conversation-observation-error.png")
        except CaptureSafetyError:
            raise
        except Exception:
            failures.append("conversation_failure_screenshot_unavailable")
    finally:
        try:
            collected = rdp.creation_observer(actor, stop=True)
            if collected.get("available"):
                observer = collected
        except Exception:
            pass
        safety_error = None
        try:
            app_state()
        except Exception:
            safety_error = CaptureSafetyError(
                "The final recording view could not be verified as clean local Logos"
            )
        if recorder is not None:
            finished_video, artifact, recording_failures = finish_screenrecord(
                recorder,
                video_path,
                uid,
                gid,
                max_bytes=MAX_VIDEO_BYTES,
                event_sink=mark,
            )
            video.update(finished_video)
            failures.extend(recording_failures)
            if artifact is not None:
                artifacts.append(artifact)
        if safety_error is not None:
            raise safety_error
    failures = list(dict.fromkeys(failures))
    scenario = {
        "verdict": "FAIL" if failures else "PASS",
        "failures": failures,
        "checkpoints": checkpoints,
        "before": before,
        "after_create": after,
        "after_reload": reloaded,
        "network": observer,
        "reload_network": reload_observer,
        "video": video,
    }
    mark("scenario_finished", {"verdict": scenario["verdict"], "failures": failures})
    timeline = out / "events.json"
    timeline.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scenario": "create_conversation",
                "events": events,
                "result": scenario,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    timeline.chmod(0o600)
    os.chown(timeline, uid, gid)
    content = timeline.read_bytes()
    artifacts.append(
        {
            "path": timeline.name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }
    )
    return actor, scenario, artifacts


def capture(out, uid, gid, metadata):
    global CAPTURE_DEADLINE
    CAPTURE_DEADLINE = time.monotonic() + 270
    rdp, actor, preparation = prepare_browser(metadata)
    device_guard()
    out.mkdir(parents=True, exist_ok=True)
    initial = rdp.state(actor)
    native_focus_is_logos()
    runtime = version()
    if not isinstance(initial.get("revision"), str) or not re.fullmatch(
        r"[0-9a-f]{40}", initial["revision"]
    ):
        raise RuntimeError("The browser revision is unknown")
    if initial["title"] != "Logos" or initial["unsafeModal"] or initial["modalOpen"]:
        raise RuntimeError("A clean Logos application view is required")
    files = []

    def snapshot(
        filename,
        surface=None,
        sidebar=None,
        tab=None,
        capture_actor=None,
        event_sink=None,
    ):
        target = capture_actor if capture_actor is not None else actor
        device_guard()
        state = rdp.state(target)
        if state["revision"] != initial["revision"] or state["visibility"] != "visible":
            raise CaptureSafetyError("Application changed during capture")
        if (
            state["unsafeModal"]
            or state["unexpectedModal"]
            or state["surfaces"] != ([surface] if surface else [])
        ):
            # Name what is on screen; a password field is reported only by its presence.
            raise CaptureSafetyError(
                "An unexpected dialog appeared: "
                + json.dumps(
                    {
                        "modals": state.get("visibleModals"),
                        "surfaces": state["surfaces"],
                        "expected": [surface] if surface else [],
                        "password_field": state["unsafeModal"],
                    },
                    ensure_ascii=False,
                )
            )
        native_focus_is_logos()
        if sidebar is not None:
            if (
                state["open"] != sidebar
                or not state["reachable"]["newChat" if sidebar else "menu"]
            ):
                raise RuntimeError(
                    "The requested sidebar control is obscured on screen"
                )
            if state["expanded"] != ("true" if sidebar else "false"):
                raise RuntimeError(
                    "Sidebar accessibility state differs from its visible state"
                )
        if tab is not None and (
            state["settingsTab"] != tab or state["settingsPanels"] != [tab]
        ):
            raise RuntimeError("The requested settings tab is not visible")
        device_guard()
        if event_sink is not None:
            event_sink("native_screencap_started:" + filename)
        png = command(ROOT + "screencap", "-p")
        if event_sink is not None:
            event_sink("native_screencap_finished:" + filename)
        if png[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("Native screenshot is not a PNG")
        width, height = struct.unpack(">II", png[16:24])
        if width != 1080 or height != 2340:
            raise RuntimeError("Unexpected Redmi 9 screen dimensions")
        path = out / filename
        path.write_bytes(png)
        path.chmod(0o600)
        os.chown(path, uid, gid)
        files.append(
            {
                "path": filename,
                "sha256": hashlib.sha256(png).hexdigest(),
                "size": len(png),
                "width": width,
                "height": height,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "browser": state,
            }
        )

    matrix_failures = []
    if initial["boot_global_missing"]:
        matrix_failures.append("document_boot_global_missing")
    if initial["core_api_missing"]:
        matrix_failures.append("document_core_api_missing")
    snapshot("00-application-initial.png")

    retried_steps = []

    def reload_app():
        nonlocal actor
        origin_before = rdp.conversation_state(actor)["time_origin_ms"]
        rdp.evaluate(
            actor,
            "JSON.stringify((()=>{setTimeout(()=>location.reload(),100);return {scheduled:true};})())",
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            device_guard()
            try:
                actor = rdp.find_logos()
                candidate = rdp.conversation_state(actor)
                if (
                    candidate["time_origin_ms"] != origin_before
                    and candidate["ready_state"] == "complete"
                ):
                    time.sleep(1.0)
                    return
            except CaptureSafetyError:
                raise
            except (OSError, RuntimeError, KeyError):
                pass
            time.sleep(0.2)
        raise RuntimeError("The application did not reload")

    def matrix_action(name, action, opener=None):
        try:
            action()
            return []
        except CaptureSafetyError:
            raise
        except Exception:
            if opener is not None:
                # One retry: reload the page and start this step again from its opening action.
                try:
                    reload_app()
                    opener()
                    action()
                    retried_steps.append(name)
                    return []
                except CaptureSafetyError:
                    raise
                except Exception:
                    pass
            failure = name + "_transition_failed"
            matrix_failures.append(failure)
            return [failure]

    def observed_snapshot(filename):
        state = rdp.state(actor)
        surfaces = state["surfaces"]
        if surfaces not in ([], ["model"], ["status"], ["settings"]):
            raise CaptureSafetyError(
                "The observed application surfaces cannot be captured safely"
            )
        surface = surfaces[0] if surfaces else None
        snapshot(filename, surface=surface)
        return files[-1]["browser"]

    def matrix_step(
        name, filename, action, surface=None, sidebar=None, tab=None, opener=None
    ):
        failures = matrix_action(name, action, opener)
        state = observed_snapshot(filename)
        if state["surfaces"] != ([surface] if surface else []):
            failures.append(name + "_surface_not_reached")
        if sidebar is not None:
            box = state["sidebar"]
            settled = box and (abs(box["x"]) < 1 if sidebar else box["right"] < 1)
            if (
                state["open"] != sidebar
                or not settled
                or not state["reachable"]["newChat" if sidebar else "menu"]
            ):
                failures.append(name + "_sidebar_not_reached")
            if state["expanded"] != ("true" if sidebar else "false"):
                failures.append(name + "_sidebar_aria_mismatch")
        if tab is not None and (
            state["settingsTab"] != tab or state["settingsPanels"] != [tab]
        ):
            failures.append(name + "_tab_not_reached")
        # Every status row must carry a known state; rows of «undefined» are a broken panel, not a ready one.
        if surface == "status" and (
            not state["statusCounts"] or state["statusCounts"].get("unknown")
        ):
            failures.append(name + "_status_rows_unknown")
        matrix_failures.extend(failures)
        files[-1]["matrix_expectation"] = {
            "state": name,
            "surface": surface,
            "sidebar": sidebar,
            "tab": tab,
            "verdict": "FAIL" if failures else "PASS",
            "failures": list(dict.fromkeys(failures)),
            "retried_after_reload": name in retried_steps,
        }

    def close_surface(name, selector):
        if name in rdp.state(actor)["surfaces"]:
            matrix_action(
                "close_" + name,
                lambda: (rdp.click(actor, selector), rdp.wait_surface(actor, None)),
            )

    surface_openers = {
        "model": lambda: (
            rdp.set_open(actor, False),
            rdp.click(actor, "#model-btn"),
            rdp.wait_surface(actor, "model"),
        ),
        "status": lambda: (
            rdp.click(actor, "#system-status-btn"),
            rdp.wait_surface(actor, "status"),
        ),
        "settings": lambda: (
            rdp.click(actor, "#settings-btn"),
            rdp.wait_surface(actor, "settings"),
        ),
    }

    def scroll_step(
        name,
        filename,
        selector,
        where,
        surface,
        opener,
        tab=None,
        before=None,
        note=None,
    ):
        """Scroll one container to its top or bottom and check that the position really changed."""
        measured = {}

        def action():
            if before is not None:
                before()
            measured.update(rdp.scroll(actor, selector, where))
            limit = measured["scrollHeight"] - measured["clientHeight"]
            if limit > 2 and (
                measured["scrollTop"] < limit - 2
                if where == "bottom"
                else measured["scrollTop"] > 0
            ):
                raise RuntimeError("scroll did not reach the " + where)

        matrix_step(name, filename, action, surface=surface, tab=tab, opener=opener)
        files[-1]["scroll"] = {
            "where": where,
            "selector": selector,
            **measured,
            "scrollable": measured.get("scrollHeight", 0)
            > measured.get("clientHeight", 0) + 2,
            **(note or {}),
        }

    chat = {}

    def load_conversation(index, conv_id):
        """Open a sidebar conversation and wait until the chat view shows that conversation's messages."""
        rdp.set_open(actor, True)
        rdp.open_conversation(actor, index)
        for _ in range(16):
            state = rdp.conversation_state(actor)
            if (
                state["current_id"] == conv_id
                and state["ready_state"] == "complete"
                and state["message_count"]
            ):
                return state["message_count"]
            time.sleep(0.5)
        return 0

    def reopen_chat():
        load_conversation(chat["index"], chat["id"])

    def choose_conversation():
        """Pick the existing conversation with the most messages; empty new chats are skipped."""
        listed = rdp.chat_candidates(actor)
        candidates = [c for c in listed if c["title"] != "Новый чат" and c["id"]][:4]
        best = None
        for candidate in candidates:
            count = load_conversation(candidate["index"], candidate["id"])
            if best is None or count > best["message_count"]:
                best = {
                    "index": candidate["index"],
                    "id": candidate["id"],
                    "title": candidate["title"],
                    "message_count": count,
                }
        if best is None or best["message_count"] < 1:
            raise RuntimeError("no existing conversation has messages")
        chat.update(best)
        reopen_chat()

    dialogs_recorder = start_screenrecord(
        out / DIALOGS_VIDEO_NAME, time_limit=180, bit_rate=1000000
    )
    for filename, wanted in (
        ("01-sidebar-open.png", True),
        ("02-sidebar-closed.png", False),
        ("03-sidebar-reopened.png", True),
    ):
        matrix_step(
            filename[:-4],
            filename,
            lambda wanted=wanted: rdp.set_open(actor, wanted),
            sidebar=wanted,
            opener=lambda wanted=wanted: rdp.set_open(actor, wanted),
        )
    matrix_step(
        "model_picker",
        "04-model-picker.png",
        surface_openers["model"],
        surface="model",
        opener=surface_openers["model"],
    )
    scroll_step(
        "model_picker_bottom",
        "19-model-picker-bottom.png",
        "#model-modal .modal-content",
        "bottom",
        surface="model",
        opener=surface_openers["model"],
    )
    close_surface("model", "#close-modal")
    matrix_step(
        "system_status",
        "05-system-status.png",
        surface_openers["status"],
        surface="status",
        opener=surface_openers["status"],
    )
    scroll_step(
        "system_status_bottom",
        "20-system-status-bottom.png",
        ".logos-system-status-content",
        "bottom",
        surface="status",
        opener=surface_openers["status"],
    )
    close_surface("status", "#logos-system-status .logos-system-status-close")
    matrix_action("open_settings", surface_openers["settings"])
    for number, tab in enumerate(("gen", "output", "routing", "adv", "theme"), start=6):
        panel = "tab-" + tab
        matrix_step(
            "settings_" + tab,
            str(number).zfill(2) + "-settings-" + tab + ".png",
            lambda panel=panel: (
                rdp.click(
                    actor,
                    '#settings-modal-custom .llm-tab-btn[data-tab="' + panel + '"]',
                ),
                rdp.wait_surface(actor, "settings", tab=panel),
            ),
            surface="settings",
            tab=panel,
            opener=surface_openers["settings"],
        )
    scroll_step(
        "settings_gen_bottom",
        "21-settings-gen-bottom.png",
        "#settings-modal-custom .settings-modal-panel",
        "bottom",
        surface="settings",
        opener=surface_openers["settings"],
        tab="tab-gen",
        before=lambda: rdp.click(
            actor, '#settings-modal-custom .llm-tab-btn[data-tab="tab-gen"]'
        ),
    )
    close_surface("settings", "#set-close-btn")
    matrix_action(
        "chat_select", choose_conversation, opener=lambda: rdp.set_open(actor, True)
    )
    scroll_step(
        "chat_top",
        "17-chat-top.png",
        "#chatbox",
        "top",
        surface=None,
        opener=reopen_chat,
        note=chat,
    )
    scroll_step(
        "chat_bottom",
        "18-chat-bottom.png",
        "#chatbox",
        "bottom",
        surface=None,
        opener=reopen_chat,
        note=chat,
    )
    matrix_action("restore_sidebar", lambda: rdp.set_open(actor, True))
    if dialogs_recorder is None:
        dialogs_video, dialogs_artifact, dialogs_failures = (
            {"path": DIALOGS_VIDEO_NAME, "available": False},
            None,
            ["native_screenrecord_unavailable"],
        )
    else:
        dialogs_video, dialogs_artifact, dialogs_failures = finish_screenrecord(
            dialogs_recorder,
            out / DIALOGS_VIDEO_NAME,
            uid,
            gid,
            max_bytes=24 * 1024 * 1024,
        )
    dialogs_artifacts = [dialogs_artifact] if dialogs_artifact else []
    matrix_failures.extend(dialogs_failures)
    device_guard()
    final = rdp.state(actor)
    if not final["open"] or final["surfaces"] or final["expanded"] != "true":
        matrix_failures.append("final_sidebar_state_not_restored")
    if final["surfaces"] in ([], ["model"]):
        actor, creation_scenario, artifacts = record_creation(
            rdp,
            actor,
            out,
            uid,
            gid,
            snapshot,
            tools_check=metadata.get("tools_self_check", False),
        )
    else:
        creation_scenario = {
            "verdict": "FAIL",
            "failures": ["creation_not_run_because_clean_view_unavailable"],
        }
        timeline = out / "events.json"
        timeline.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "scenario": "create_conversation",
                    "events": [],
                    "result": creation_scenario,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        timeline.chmod(0o600)
        os.chown(timeline, uid, gid)
        content = timeline.read_bytes()
        artifacts = [
            {
                "path": timeline.name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        ]
    artifacts = dialogs_artifacts + artifacts
    try:
        final_sidebar_open = rdp.state(actor)["open"]
    except (OSError, RuntimeError, KeyError):
        final_sidebar_open = None
    rdp.s.close()
    by_name = {entry["path"]: entry for entry in files}
    if (
        by_name["01-sidebar-open.png"]["sha256"]
        == by_name["02-sidebar-closed.png"]["sha256"]
    ):
        matrix_failures.append("sidebar_open_closed_native_screenshots_identical")
    matrix_failures = list(dict.fromkeys(matrix_failures))
    # The verdicts describe the installed app. They prove the workflow revision only when that is what runs.
    deployment_acceptance = {
        "status": "CURRENT"
        if runtime["runtime_revision"] == metadata.get("workflow_revision")
        else "STALE_RUNTIME",
        "runtime_revision": runtime["runtime_revision"],
        "workflow_revision": metadata.get("workflow_revision"),
        "scope": "matrix and creation verdicts describe the installed runtime; they prove the workflow revision only when CURRENT",
    }
    manifest = {
        "schema_version": 2,
        "device": "Redmi 9",
        "browser": "Iceraven",
        "capture_method": "Android screencap",
        "interaction_method": "Firefox DevTools button click",
        "runtime_revision": runtime["runtime_revision"],
        "browser_revision": initial["revision"],
        "revision_match": initial["revision"] == runtime["runtime_revision"],
        "deployment_acceptance": deployment_acceptance,
        "ui_version": runtime.get("ui_version"),
        "application_process_id": runtime.get("process_id"),
        "preparation": preparation,
        "captured_at": files[0]["captured_at"],
        "initial_sidebar_open": initial["open"],
        "final_sidebar_open": final_sidebar_open,
        "files": files,
        "matrix_scenario": {
            "verdict": "FAIL" if matrix_failures else "PASS",
            "failures": matrix_failures,
        },
        "creation_scenario": creation_scenario,
        "dialogs_video": dialogs_video,
        "artifacts": artifacts,
        **metadata,
    }
    path = out / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)
    os.chown(path, uid, gid)
    os.chown(out, uid, gid)
    print(
        json.dumps(
            {
                "captured": len(files),
                "runtime_revision": runtime["runtime_revision"],
                "deployment_acceptance": deployment_acceptance["status"],
            }
        )
    )


def restore_clean_page(target_url=None):
    """Leave the phone on the plain app page: no open picker and no capture query in the address bar."""
    # The capture deadline may already be spent here, so the restore gets its own short budget.
    try:
        rdp = RDP(deadline=time.monotonic() + 15)
        try:
            actor = rdp.find_logos()
            rdp.evaluate(
                actor,
                "JSON.stringify((()=>{setTimeout(()=>location.replace("
                + json.dumps(target_url or LOGOS_URL)
                + "),100);return {scheduled:true};})())",
            )
        finally:
            rdp.s.close()
    except Exception as error:
        print("Clean page restore failed: " + str(error), file=sys.stderr)


def main():
    global LOGOS_URL, CAPTURE_STORAGE_ROOT
    # Both the runner driver and its root child enforce the same storage budget.
    if len(sys.argv) > 2 and sys.argv[1] == "_capture":
        capture_output = Path(sys.argv[2]).resolve()
    elif len(sys.argv) == 2 and not sys.argv[1].startswith("_"):
        capture_output = Path(sys.argv[1]).resolve()
    else:
        capture_output = None
    if capture_output is not None:
        # Media is written only to the fixed-size RAM disk (scripts/redmi_media_ram.sh), never to flash.
        # Its size is the hard storage limit, so the free-space and budget walk is not needed there.
        require_ram_disk(capture_output)
    if len(sys.argv) == 6 and sys.argv[1] == "_capture":
        metadata = json.loads(sys.argv[5])
        LOGOS_URL = capture_url(metadata.get("capture_url", LOGOS_URL))
        try:
            capture(Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), metadata)
        finally:
            restore_clean_page(
                "http://127.0.0.1:8080/" if LOGOS_URL.endswith(":8081/") else LOGOS_URL
            )
        return
    required = (
        "GITHUB_ACTIONS",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "RUNNER_NAME",
        "RUNNER_TEMP",
        "GITHUB_SHA",
    )
    if os.environ.get("GITHUB_ACTIONS") != "true" or any(
        not os.environ.get(k) for k in required
    ):
        raise RuntimeError(
            "Run screenshot capture inside the existing GitHub Actions runner"
        )
    if len(sys.argv) != 2:
        raise RuntimeError("Usage: redmi_screenshot_capture.py OUTPUT_DIRECTORY")
    output = Path(sys.argv[1]).resolve()
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    if runner_temp not in output.parents:
        raise RuntimeError("Capture output must be within RUNNER_TEMP")
    metadata = {
        "github_run_id": os.environ["GITHUB_RUN_ID"],
        "github_run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "workflow_revision": os.environ["GITHUB_SHA"],
        "runner_name": os.environ["RUNNER_NAME"],
        "github_run_url": "https://github.com/maksimp6/Chat/actions/runs/"
        + os.environ["GITHUB_RUN_ID"],
    }
    metadata["tools_self_check"] = os.environ.get("LOGOS_TOOLS_SELF_CHECK") == "1"
    metadata["capture_url"] = capture_url(
        os.environ.get("LOGOS_CAPTURE_URL", LOGOS_URL)
    )
    # -B: the root child must not write root-owned __pycache__ that the runner user cannot delete.
    argv = [
        sys.executable,
        "-B",
        str(Path(__file__).resolve()),
        "_capture",
        str(output),
        str(os.getuid()),
        str(os.getgid()),
        json.dumps(metadata),
    ]
    safe_env = {
        "PATH": os.environ.get("PATH", "/system/bin"),
        "TMPDIR": os.environ.get("TMPDIR", str(runner_temp)),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        ["su", "-c", shlex.join(argv)], env=safe_env, capture_output=True, timeout=300
    )
    if result.returncode:
        # No job token is in the capture child's environment or argv.
        raise RuntimeError(
            result.stderr.decode(errors="replace")[-1800:] or "Capture failed"
        )
    if result.stderr:
        # Forward non-fatal child messages, such as a failed return to the clean page.
        print(result.stderr.decode(errors="replace")[-1800:], file=sys.stderr)
    print(result.stdout.decode().strip())


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Screenshot capture failed: " + str(error), file=sys.stderr)
        sys.exit(1)
