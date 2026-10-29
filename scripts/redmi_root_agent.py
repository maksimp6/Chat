#!/usr/bin/env python3
"""Runner-only native RootAgent CLI for the owner's rooted Redmi 9.

Drives the whole phone as a user: absolute tap, swipe, text and key input go
through root /system/bin/input; capture reads the screen; move/click keep the
legacy relative input through the existing root mouse daemon. submitted means
the command was sent, never that Android visibly applied the action.
"""

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import select
import shlex
import socket
import stat
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

MOUSE = "/data/adb/alice-mouse/control.sock"
DAEMON_SHA256 = "9d25ff623eb29fc9b9f72e9e2126d52144db2181508229aac91eb4cd0de38516"
PNG_LIMIT = 9 * 1024 * 1024
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")
RUNNER_ENV_KEYS = (
    "CI",
    "GITHUB_ACTIONS",
    "GITHUB_RUN_ID",
    "GITHUB_SHA",
    "RUNNER_NAME",
    "RUNNER_TEMP",
)


class AgentError(Exception):
    pass


def runner_guard():
    values = {key: os.environ.get(key, "") for key in RUNNER_ENV_KEYS}
    if (
        values["CI"] != "true"
        or values["GITHUB_ACTIONS"] != "true"
        or not all(values.values())
    ):
        raise AgentError("actual_github_runner_required")
    return values


def bounded_command(argv, limit=2 * 1024 * 1024, timeout=8):
    """Read bounded stdout, without exposing command stderr or using a shell."""
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    deadline, output = time.monotonic() + timeout, bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if (
                remaining <= 0
                or not select.select([process.stdout], [], [], remaining)[0]
            ):
                raise AgentError("android_command_timeout")
            chunk = os.read(
                process.stdout.fileno(), min(65536, limit + 1 - len(output))
            )
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > limit:
                raise AgentError("android_command_output_limit")
        if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            raise AgentError("android_command_failed")
        return bytes(output)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        process.stdout.close()


def device_guard():
    if os.geteuid() != 0:
        raise AgentError("root_worker_required")
    values = [
        bounded_command(["/system/bin/getprop", key], 128).decode().strip()
        for key in ("ro.product.model", "ro.product.device")
    ]
    if values != ["Redmi 9", "galahad"]:
        raise AgentError("wrong_device")


def screen_state():
    policy = bounded_command(["/system/bin/dumpsys", "window", "policy"]).decode(
        "utf-8", "replace"
    )
    flags = []
    for key in ("showing", "mInputRestricted", "mKeyguardOccluded"):
        values = re.findall(
            r"(?<![A-Za-z0-9_])" + key + r"\s*=\s*(true|false)\b", policy
        )
        flags.append(bool(values) and all(value == "false" for value in values))
    window = bounded_command(["/system/bin/dumpsys", "window"]).decode(
        "utf-8", "replace"
    )
    display_source = window
    display_ids = set(re.findall(r"\bmDisplayId\s*=\s*(\d+)\b", window))
    if len(display_ids) > 1:
        blocks = re.split(r"(?m)(?=^\s*Display:\s*mDisplayId=)", window)
        primary = [
            block
            for block in blocks
            if re.match(r"\s*Display:\s*mDisplayId=0\b", block)
        ]
        display_source = primary[0] if len(primary) == 1 else ""
    elif display_ids and "0" not in display_ids:
        display_source = ""
    display_flags = []
    for key in ("mScreenOnEarly", "mScreenOnFully"):
        values = re.findall(
            r"(?<![A-Za-z0-9_])" + key + r"\s*=\s*(true|false)\b", display_source
        )
        display_flags.append(bool(values) and all(value == "true" for value in values))
    windows = re.findall(r"(?m)^\s*mCurrentFocus=([^\r\n]*)", window)
    focus_window = windows[0].strip() if len(windows) == 1 else None
    focus = re.search(r"\b(" + PACKAGE.pattern + r")/", focus_window or "")
    return {
        "unlocked": all(flags),
        "display_on": all(display_flags),
        "package": focus.group(1) if focus else None,
        "focus_window": focus_window if focus else None,
    }


def require_screen(expected=None):
    state = screen_state()
    if state.get("display_on") is not True:
        raise AgentError("screen_not_awake")
    if not state["unlocked"] or not state["package"] or not state.get("focus_window"):
        raise AgentError("screen_locked_or_unknown")
    if expected is not None and state["package"] != expected:
        raise AgentError("foreground_package_mismatch")
    return state


def screen_size():
    """Current display size in pixels, for bounding absolute coordinates."""
    out = bounded_command(["/system/bin/wm", "size"], 256).decode("utf-8", "replace")
    match = re.search(r"Override size:\s*(\d+)x(\d+)", out) or re.search(
        r"Physical size:\s*(\d+)x(\d+)", out
    )
    if match is None:
        raise AgentError("screen_size_unknown")
    return int(match.group(1)), int(match.group(2))


def _bounded_int(value, low, high):
    return type(value) is int and low <= value <= high


KEYCODE = re.compile(r"KEYCODE_[A-Z0-9_]+|[0-9]{1,4}")
URL = re.compile(r"https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:[/?#][^\s]*)?")


def wakefulness():
    """PowerManager's wakefulness (Awake, Asleep, Dozing, Dreaming), or None."""
    power = bounded_command(["/system/bin/dumpsys", "power"]).decode("utf-8", "replace")
    match = re.search(r"\bmWakefulness=(\w+)", power)
    return match.group(1) if match else None


def power_key_device():
    """The kernel input device that reports KEY_POWER, from getevent -pl."""
    listing = bounded_command(["/system/bin/getevent", "-pl"], 512 * 1024).decode(
        "utf-8", "replace"
    )
    device = None
    for line in listing.splitlines():
        match = re.match(r"\s*add device \d+: (/dev/input/event\d+)", line)
        if match:
            device = match.group(1)
        elif device and re.search(r"\bKEY_POWER\b", line):
            return device
    return None


def press_power_key():
    """One real KEY_POWER press and release on the power key's own input device."""
    device = power_key_device()
    if device is None:
        raise AgentError("power_key_device_not_found")
    for code, value in (("116", "1"), ("116", "0")):
        bounded_command(["/system/bin/sendevent", device, "1", code, value])
        bounded_command(["/system/bin/sendevent", device, "0", "0", "0"])


def browser_component(url):
    """An explicit browser activity for the URL, so Android shows no app chooser."""
    try:
        listing = bounded_command(
            [
                "/system/bin/cmd",
                "package",
                "query-activities",
                "--brief",
                "-a",
                "android.intent.action.VIEW",
                "-c",
                "android.intent.category.BROWSABLE",
                "-d",
                url,
            ],
            256 * 1024,
        ).decode("utf-8", "replace")
    except AgentError:
        return None
    found = [
        item
        for item in re.findall(r"\b([A-Za-z][\w.]*/[\w.$]+)", listing)
        if not item.startswith("android/")
    ]
    preferred = [item for item in found if "iceraven" in item.lower()]
    return (preferred or found or [None])[0]


def open_url(url):
    if not isinstance(url, str) or len(url) > 2048 or URL.fullmatch(url) is None:
        raise AgentError("invalid_url")
    component = browser_component(url)
    start = ["/system/bin/am", "start", "-a", "android.intent.action.VIEW", "-d", url]
    if component:
        start += ["-n", component]
    bounded_command(start)
    return component


# --- Kernel-level input through /dev/uinput -------------------------------------
# This MIUI build silently drops events injected with /system/bin/input (exit 0,
# no effect), while the uinput mouse is honoured. So taps, swipes, keys and text
# go through virtual uinput devices that Android reads like real hardware.
UINPUT = "/dev/uinput"
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045567
UI_SET_PROPBIT, UI_DEV_CREATE, UI_DEV_DESTROY = 0x4004556E, 0x5501, 0x5502
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
BTN_TOUCH, INPUT_PROP_DIRECT = 0x14A, 0x01
ABS_X, ABS_Y = 0x00, 0x01
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y = 0x2F, 0x35, 0x36
ABS_MT_TRACKING_ID = 0x39
KEY_LEFTSHIFT = 42
# Linux key codes for ASCII on a US layout (Android's Generic.kcm): char -> (code, shift).
_PLAIN = {
    **dict(zip("abcdefghijklmnopqrstuvwxyz",
               (30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50,
                49, 24, 25, 16, 19, 31, 20, 22, 47, 17, 45, 21, 44))),
    **dict(zip("1234567890", range(2, 12))),
    " ": 57, "-": 12, "=": 13, "[": 26, "]": 27, ";": 39, "'": 40, "`": 41,
    "\\": 43, ",": 51, ".": 52, "/": 53, "\t": 15,
}
_SHIFTED = dict(zip('!@#$%^&*()_+{}:"~|<>?', (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13,
                                              26, 27, 39, 40, 41, 43, 51, 52, 53)))
ASCII_KEYS = {c: (k, False) for c, k in _PLAIN.items()}
ASCII_KEYS.update({c.upper(): (k, True) for c, k in _PLAIN.items() if c.isalpha()})
ASCII_KEYS.update({c: (k, True) for c, k in _SHIFTED.items()})
# Android key names and numbers -> Linux key codes (Generic.kl).
ANDROID_KEYS = {
    "HOME": 172, "BACK": 158, "ENTER": 28, "DEL": 14, "FORWARD_DEL": 111,
    "TAB": 15, "SPACE": 57, "ESCAPE": 1, "MENU": 139, "POWER": 116,
    "WAKEUP": 143, "VOLUME_UP": 115, "VOLUME_DOWN": 114, "VOLUME_MUTE": 113,
    "DPAD_UP": 103, "DPAD_DOWN": 108, "DPAD_LEFT": 105, "DPAD_RIGHT": 106,
    "APP_SWITCH": 580, "SEARCH": 217, "MOVE_HOME": 102, "MOVE_END": 107,
    "PAGE_UP": 104, "PAGE_DOWN": 109,
    **{c.upper(): k for c, k in _PLAIN.items() if c.isalnum()},
}
ANDROID_NUMBERS = {
    3: "HOME", 4: "BACK", 19: "DPAD_UP", 20: "DPAD_DOWN", 21: "DPAD_LEFT",
    22: "DPAD_RIGHT", 24: "VOLUME_UP", 25: "VOLUME_DOWN", 26: "POWER",
    61: "TAB", 62: "SPACE", 66: "ENTER", 67: "DEL", 82: "MENU", 84: "SEARCH",
    111: "ESCAPE", 112: "FORWARD_DEL", 164: "VOLUME_MUTE", 187: "APP_SWITCH",
    224: "WAKEUP",
}


def linux_key(token):
    """The Linux key code for KEYCODE_NAME, a bare name, or an Android key number."""
    text = str(token)
    if text.isdigit():
        name = ANDROID_NUMBERS.get(int(text))
    else:
        name = text.removeprefix("KEYCODE_")
    if name not in ANDROID_KEYS:
        raise AgentError("unsupported_keycode")
    return ANDROID_KEYS[name]


class VirtualDevice:
    """One uinput device that lives only for this call."""

    def __init__(self, name, keys=(), size=None):
        import fcntl

        self._ioctl = fcntl.ioctl
        self.fd = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK)
        try:
            self._ioctl(self.fd, UI_SET_EVBIT, EV_SYN)
            absmax = [0] * 64
            if keys:
                self._ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
                for code in keys:
                    self._ioctl(self.fd, UI_SET_KEYBIT, code)
            if size:
                width, height = size
                self._ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
                self._ioctl(self.fd, UI_SET_KEYBIT, BTN_TOUCH)
                self._ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
                for axis in (ABS_X, ABS_Y, ABS_MT_SLOT, ABS_MT_POSITION_X,
                             ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID):
                    self._ioctl(self.fd, UI_SET_ABSBIT, axis)
                self._ioctl(self.fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT)
                absmax[ABS_X] = absmax[ABS_MT_POSITION_X] = width - 1
                absmax[ABS_Y] = absmax[ABS_MT_POSITION_Y] = height - 1
                absmax[ABS_MT_TRACKING_ID] = 65535
            # struct uinput_user_dev: name, input_id (BUS_VIRTUAL), ff_effects_max,
            # absmax, absmin, absfuzz, absflat.
            os.write(self.fd, struct.pack(
                "80sHHHHI64i64i64i64i", name.encode("ascii"), 0x06, 0x1D6B, 0x0104,
                1, 0, *absmax, *([0] * 64), *([0] * 64), *([0] * 64)))
            self._ioctl(self.fd, UI_DEV_CREATE)
        except Exception:
            os.close(self.fd)
            raise
        # Android's InputReader needs a moment to add the new device.
        time.sleep(1.5)

    def emit(self, kind, code, value):
        os.write(self.fd, struct.pack("llHHi", 0, 0, kind, code, value))

    def sync(self):
        self.emit(EV_SYN, 0, 0)

    def close(self):
        time.sleep(0.5)
        try:
            self._ioctl(self.fd, UI_DEV_DESTROY)
        finally:
            os.close(self.fd)


class Injector:
    """Touch and keyboard input for one call; devices are created on first use."""

    def __init__(self, width, height):
        self.size = (width, height)
        self.touch = None
        self.keys = None
        self.tracking = 0

    def _touch(self):
        if self.touch is None:
            self.touch = VirtualDevice("rootagent-touch", size=self.size)
        return self.touch

    def _keyboard(self):
        if self.keys is None:
            # Every key that text or key can press must be declared, punctuation included.
            codes = {
                KEY_LEFTSHIFT,
                *ANDROID_KEYS.values(),
                *(code for code, _ in ASCII_KEYS.values()),
            }
            self.keys = VirtualDevice("rootagent-keys", keys=sorted(codes))
        return self.keys

    def _point(self, device, x, y):
        device.emit(EV_ABS, ABS_MT_POSITION_X, x)
        device.emit(EV_ABS, ABS_MT_POSITION_Y, y)
        device.emit(EV_ABS, ABS_X, x)
        device.emit(EV_ABS, ABS_Y, y)

    def _down(self, x, y):
        device = self._touch()
        self.tracking += 1
        device.emit(EV_ABS, ABS_MT_SLOT, 0)
        device.emit(EV_ABS, ABS_MT_TRACKING_ID, self.tracking)
        self._point(device, x, y)
        device.emit(EV_KEY, BTN_TOUCH, 1)
        device.sync()
        return device

    def _up(self, device):
        device.emit(EV_ABS, ABS_MT_TRACKING_ID, -1)
        device.emit(EV_KEY, BTN_TOUCH, 0)
        device.sync()

    def tap(self, x, y):
        device = self._down(x, y)
        time.sleep(0.08)
        self._up(device)
        time.sleep(0.15)

    def swipe(self, x, y, x2, y2, duration_ms=300):
        device = self._down(x, y)
        steps = max(8, min(120, duration_ms // 16))
        for index in range(1, steps + 1):
            self._point(device, x + (x2 - x) * index // steps, y + (y2 - y) * index // steps)
            device.sync()
            time.sleep(duration_ms / 1000 / steps)
        self._up(device)
        time.sleep(0.15)

    def press(self, code, shift=False):
        device = self._keyboard()
        if shift:
            device.emit(EV_KEY, KEY_LEFTSHIFT, 1)
            device.sync()
        device.emit(EV_KEY, code, 1)
        device.sync()
        device.emit(EV_KEY, code, 0)
        device.sync()
        if shift:
            device.emit(EV_KEY, KEY_LEFTSHIFT, 0)
            device.sync()
        time.sleep(0.03)

    def key(self, token):
        self.press(linux_key(token))

    def text(self, value):
        for char in value:
            self.press(*ASCII_KEYS[char])

    def close(self):
        for device in (self.touch, self.keys):
            if device is not None:
                device.close()


def input_report(text):
    """The parts of dumpsys input that explain where injected touches went."""
    lines = text.splitlines()
    keep = []
    for index, line in enumerate(lines):
        if "rootagent" in line:
            keep.extend(lines[max(0, index - 1) : index + 40])
    marker = next(
        (i for i, line in enumerate(lines) if "Input Dispatcher State" in line), None
    )
    if marker is not None:
        keep.append("----")
        keep.extend(lines[marker : marker + 120])
    return "\n".join(keep)[:60000]


def check_text(value):
    """ASCII on a US layout is what a virtual keyboard can type."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or any(char not in ASCII_KEYS for char in value)
    ):
        raise AgentError("text_must_be_ascii_keyboard_characters")


def run_step(step, injector, width, height, diagnostics=None):
    """One user action of a batch: tap, swipe, text, key, wait or browse."""
    if not isinstance(step, dict) or len(step) != 1:
        raise AgentError("invalid_batch_step")
    ((kind, value),) = step.items()
    if kind == "tap":
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not _bounded_int(value[0], 0, width)
            or not _bounded_int(value[1], 0, height)
        ):
            raise AgentError("invalid_batch_tap")
        injector.tap(value[0], value[1])
    elif kind == "swipe":
        limits = (width, height, width, height, 60000)
        if (
            not isinstance(value, list)
            or len(value) not in (4, 5)
            or any(not _bounded_int(v, 0, lim) for v, lim in zip(value, limits))
        ):
            raise AgentError("invalid_batch_swipe")
        injector.swipe(*value[:4], value[4] if len(value) == 5 else 300)
    elif kind == "text":
        check_text(value)
        injector.text(value)
    elif kind == "key":
        injector.key(value)
    elif kind == "diag":
        # dumpsys input while this call's virtual devices exist: whether Android
        # registered them, which display they map to, and where events went.
        if diagnostics is None:
            raise AgentError("invalid_batch_step")
        report = bounded_command(["/system/bin/dumpsys", "input"], 4 * 1024 * 1024, 15)
        diagnostics.append(input_report(report.decode("utf-8", "replace")))
    elif kind == "wait":
        if not _bounded_int(value, 0, 120000):
            raise AgentError("invalid_batch_wait")
        time.sleep(value / 1000)
    elif kind == "browse":
        open_url(value)
    else:
        raise AgentError("invalid_batch_step")
    return kind


def mouse_nudge():
    with mouse_connection() as connection:
        submit_mouse(connection, "move", 1, 1)


def wake_display():
    """Wake the display within this call; a separate run would see it time out again.

    Injected KEYCODE_WAKEUP does not wake this MIUI device, so it falls back to a
    move through the existing uinput mouse daemon and then to a real KEY_POWER
    press on the power key's input device. Each one is tried only while the
    device is not Awake, so a toggle never turns a woken screen off again.
    """
    attempts = []
    actions = (
        ("mouse", mouse_nudge),
        ("power_key", press_power_key),
        ("keyevent", lambda: bounded_command(
            ["/system/bin/input", "keyevent", "KEYCODE_WAKEUP"])),
    )
    for name, action in actions:
        if wakefulness() == "Awake":
            break
        try:
            action()
        except (AgentError, OSError) as error:
            attempts.append(f"{name}: {error}")
            continue
        time.sleep(1.5)
        attempts.append(f"{name}: {wakefulness()}")
    return wakefulness(), attempts


def validate_png(png):
    if not 8 < len(png) <= PNG_LIMIT or png[:8] != b"\x89PNG\r\n\x1a\n":
        raise AgentError("invalid_png")
    offset, header, compressed, ended, palette, finished = (
        8,
        None,
        [],
        False,
        False,
        False,
    )
    while offset < len(png):
        if offset + 12 > len(png):
            raise AgentError("truncated_png")
        size, kind = struct.unpack(">I4s", png[offset : offset + 8])
        end = offset + size + 12
        if end > len(png) or not re.fullmatch(b"[A-Za-z]{4}", kind) or kind[2] & 32:
            raise AgentError("invalid_png_chunk")
        data = png[offset + 8 : end - 4]
        if (
            binascii.crc32(kind + data) & 0xFFFFFFFF
            != struct.unpack(">I", png[end - 4 : end])[0]
        ):
            raise AgentError("png_crc_mismatch")
        if header is None and kind != b"IHDR":
            raise AgentError("missing_png_header")
        if kind == b"IHDR":
            if header is not None or offset != 8 or size != 13:
                raise AgentError("invalid_png_header")
            width, height, depth, color, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", data)
            )
            if not (
                0 < width <= 8192
                and 0 < height <= 8192
                and width * height <= 16 * 1024 * 1024
            ):
                raise AgentError("png_dimensions_limit")
            if (
                depth != 8
                or color not in (2, 6)
                or (compression, filtering, interlace) != (0, 0, 0)
            ):
                raise AgentError("unsupported_png_profile")
            header = (width, height, 3 if color == 2 else 4)
        elif kind == b"IDAT":
            if ended:
                raise AgentError("noncontiguous_png_data")
            compressed.append(data)
        elif kind == b"IEND":
            if size or not compressed or end != len(png):
                raise AgentError("invalid_png_end")
            finished = True
        elif kind == b"PLTE":
            if palette or compressed or not 0 < size <= 768 or size % 3:
                raise AgentError("invalid_png_palette")
            palette = True
        elif not kind[0] & 32:
            raise AgentError("unknown_critical_png_chunk")
        if compressed and kind != b"IDAT":
            ended = True
        offset = end
    if not finished or header is None:
        raise AgentError("incomplete_png")
    width, height, channels = header
    stride = width * channels + 1
    expected = stride * height
    decoder = zlib.decompressobj()
    decoded = decoder.decompress(b"".join(compressed), expected + 1)
    if (
        len(decoded) != expected
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
        or any(decoded[i] > 4 for i in range(0, expected, stride))
    ):
        raise AgentError("invalid_png_pixels")
    return {
        "width": width,
        "height": height,
        "bytes": len(png),
        "sha256": hashlib.sha256(png).hexdigest(),
    }


def mouse_metadata():
    directory, endpoint = os.lstat(str(Path(MOUSE).parent)), os.lstat(MOUSE)
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != 0
        or stat.S_IMODE(directory.st_mode) != 0o700
        or not stat.S_ISSOCK(endpoint.st_mode)
        or endpoint.st_uid != 0
        or stat.S_IMODE(endpoint.st_mode) != 0o600
    ):
        raise AgentError("unsafe_mouse_socket_metadata")


def mouse_connection():
    mouse_metadata()
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(2)
    try:
        connection.connect(MOUSE)
        pid, uid, _ = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        if uid != 0 or pid <= 0:
            raise AgentError("mouse_peer_not_root")
        with open(f"/proc/{pid}/exe", "rb") as executable:
            if not stat.S_ISREG(os.fstat(executable.fileno()).st_mode):
                raise AgentError("invalid_mouse_executable")
            digest, count = hashlib.sha256(), 0
            for chunk in iter(lambda: executable.read(65536), b""):
                count += len(chunk)
                if count > 32 * 1024 * 1024:
                    raise AgentError("mouse_executable_limit")
                digest.update(chunk)
        if digest.hexdigest() != DAEMON_SHA256:
            raise AgentError("mouse_executable_mismatch")
        return connection
    except Exception:
        connection.close()
        raise


def submit_mouse(connection, command, dx, dy):
    """One internal protocol write; an uncertain delivery must never be retried."""
    try:
        connection.sendall(f"{command} {dx} {dy}\n".encode("ascii"))
        reply = bytearray()
        while len(reply) < 16 and not reply.endswith(b"\n"):
            chunk = connection.recv(16 - len(reply))
            if not chunk:
                break
            reply.extend(chunk)
    except OSError:
        raise AgentError("mouse_delivery_unknown_do_not_retry") from None
    if reply != b"OK\n":
        raise AgentError("mouse_ack_unconfirmed_do_not_retry")


def worker(request):
    command = request.get("command")
    # Required and optional request keys per command. The owner drives the whole
    # phone as a user: absolute tap/swipe, text and key input go through root
    # /system/bin/input, which injects trusted events across the whole device.
    required = {
        "status": set(),
        "probe": set(),
        "wake": set(),
        "capture": set(),
        "move": {"dx", "dy"},
        "click": {"dx", "dy"},
        "tap": {"x", "y"},
        "swipe": {"x", "y", "x2", "y2"},
        "text": {"text"},
        "key": {"keycode"},
        "browse": {"url"},
        "batch": {"steps"},
    }
    optional = {
        "capture": {"expected_package"},
        "move": {"expected_package"},
        "click": {"expected_package"},
        "swipe": {"duration_ms"},
        "browse": {"wait_ms"},
    }
    if command not in required:
        raise AgentError("invalid_worker_request")
    allowed = {"command"} | required[command] | optional.get(command, set())
    present = set(request)
    if not ({"command"} | required[command]) <= present <= allowed:
        raise AgentError("invalid_worker_request")
    device_guard()
    if command == "status":
        state = screen_state()
        try:
            mouse_metadata()
            mouse_ready = True
        except (OSError, AgentError):
            mouse_ready = False
        try:
            width, height = screen_size()
        except AgentError:
            width = height = None
        return {
            "model": "Redmi 9",
            "device": "galahad",
            "root_uid": 0,
            "keyguard_locked_or_unknown": not state["unlocked"],
            "display_on": state["display_on"],
            "focus_present": bool(state["package"]),
            "focus_package": state["package"],
            "screen_width": width,
            "screen_height": height,
            "mouse_metadata_valid": mouse_ready,
        }
    if command == "probe":
        with mouse_connection():
            return {
                "root_uid": 0,
                "mouse_peer_verified": True,
                "daemon_sha256": DAEMON_SHA256,
                "input_operations": "NOT_RUN",
            }
    if command == "wake":

        def already_awake():
            return {
                "command": "wake",
                "already_awake": True,
                "input_operations": "NOT_RUN",
                "verified": False,
            }

        if screen_state().get("display_on") is True:
            return already_awake()
        with mouse_connection() as connection:
            if screen_state().get("display_on") is True:
                return already_awake()
            submit_mouse(connection, "move", 1, 1)
        return {
            "command": "wake",
            "submitted": True,
            "verified": False,
            "coordinates": "relative",
        }
    if command == "tap":
        width, height = screen_size()
        x, y = request["x"], request["y"]
        if not _bounded_int(x, 0, width) or not _bounded_int(y, 0, height):
            raise AgentError("coordinates_out_of_bounds")
        injector = Injector(width, height)
        try:
            injector.tap(x, y)
        finally:
            injector.close()
        return {
            "command": "tap",
            "x": x,
            "y": y,
            "submitted": True,
            "verified": False,
            "coordinates": "absolute",
        }
    if command == "swipe":
        width, height = screen_size()
        x, y, x2, y2 = (request[key] for key in ("x", "y", "x2", "y2"))
        if any(
            not _bounded_int(value, 0, limit)
            for value, limit in ((x, width), (y, height), (x2, width), (y2, height))
        ):
            raise AgentError("coordinates_out_of_bounds")
        duration = request.get("duration_ms")
        if duration is not None and not _bounded_int(duration, 1, 60000):
            raise AgentError("invalid_swipe_duration")
        injector = Injector(width, height)
        try:
            injector.swipe(x, y, x2, y2, duration or 300)
        finally:
            injector.close()
        return {
            "command": "swipe",
            "from": [x, y],
            "to": [x2, y2],
            "duration_ms": duration,
            "submitted": True,
            "verified": False,
            "coordinates": "absolute",
        }
    if command == "text":
        value = request["text"]
        check_text(value)
        injector = Injector(*screen_size())
        try:
            injector.text(value)
        finally:
            injector.close()
        # The text itself is the owner's input and is never echoed into the result.
        return {
            "command": "text",
            "length": len(value),
            "submitted": True,
            "verified": False,
        }
    if command == "key":
        code = request["keycode"]
        if type(code) is int and 0 <= code <= 9999:
            token = str(code)
        elif isinstance(code, str) and KEYCODE.fullmatch(code) is not None:
            token = code
        else:
            raise AgentError("invalid_keycode")
        injector = Injector(*screen_size())
        try:
            injector.key(token)
        finally:
            injector.close()
        return {
            "command": "key",
            "keycode": token,
            "submitted": True,
            "verified": False,
        }
    if command == "browse":
        url = request["url"]
        if not isinstance(url, str) or len(url) > 2048 or URL.fullmatch(url) is None:
            raise AgentError("invalid_url")
        wait = request.get("wait_ms", 6000)
        if not _bounded_int(wait, 0, 30000):
            raise AgentError("invalid_wait")
        # Wake, open and capture in one call: between separate runs the screen
        # times out again, which is why a lone capture came back black.
        woken, wake_attempts = wake_display()
        try:
            bounded_command(["/system/bin/wm", "dismiss-keyguard"])
        except AgentError:
            pass
        component = open_url(url)
        time.sleep(wait / 1000)
        png = bounded_command(["/system/bin/screencap", "-p"], PNG_LIMIT, 15)
        metadata = validate_png(png)
        state = screen_state()
        return {
            **metadata,
            "command": "browse",
            "url": url,
            "wakefulness": woken,
            "wake_attempts": wake_attempts,
            "browser": component,
            "display_on": state["display_on"],
            "keyguard_locked_or_unknown": not state["unlocked"],
            "focus_package": state["package"],
            "png_base64": base64.b64encode(png).decode("ascii"),
        }
    if command == "batch":
        steps = request["steps"]
        if not isinstance(steps, list) or not 1 <= len(steps) <= 30:
            raise AgentError("invalid_batch")
        waits = sum(
            s.get("wait", 0) for s in steps if isinstance(s, dict) and type(s.get("wait")) is int
        )
        if waits > 150000:
            raise AgentError("batch_wait_limit")
        # All actions in one call, so the screen cannot time out between them.
        woken, wake_attempts = wake_display()
        try:
            bounded_command(["/system/bin/wm", "dismiss-keyguard"])
        except AgentError:
            pass
        width, height = screen_size()
        injector = Injector(width, height)
        diagnostics = []
        try:
            done = [run_step(step, injector, width, height, diagnostics) for step in steps]
        finally:
            injector.close()
        png = bounded_command(["/system/bin/screencap", "-p"], PNG_LIMIT, 15)
        metadata = validate_png(png)
        state = screen_state()
        return {
            **metadata,
            "command": "batch",
            "steps_done": done,
            "diagnostics": diagnostics,
            "wakefulness": woken,
            "wake_attempts": wake_attempts,
            "display_on": state["display_on"],
            "focus_package": state["package"],
            "png_base64": base64.b64encode(png).decode("ascii"),
        }
    if command == "capture":
        expected = request.get("expected_package")
        if "expected_package" in request and (
            not isinstance(expected, str) or PACKAGE.fullmatch(expected) is None
        ):
            raise AgentError("invalid_capture_request")
        # A capture is never blocked by screen state: it wakes the display and
        # returns whatever it shows. Any expected package is reported, not enforced.
        woken, wake_attempts = wake_display()
        png = bounded_command(["/system/bin/screencap", "-p"], PNG_LIMIT, 15)
        metadata = validate_png(png)
        state = screen_state()
        if expected is not None:
            metadata["expected_package_matches"] = state["package"] == expected
        return {
            **metadata,
            "wakefulness": woken,
            "wake_attempts": wake_attempts,
            "focus_package": state["package"],
            "png_base64": base64.b64encode(png).decode("ascii"),
        }
    # move / click: legacy relative-delta input through the existing C daemon.
    if any(
        type(request[key]) is not int or not -500 <= request[key] <= 500
        for key in ("dx", "dy")
    ):
        raise AgentError("invalid_mouse_request")
    expected = request.get("expected_package")
    if expected is not None and (
        not isinstance(expected, str) or PACKAGE.fullmatch(expected) is None
    ):
        raise AgentError("invalid_mouse_request")
    with mouse_connection() as connection:
        submit_mouse(connection, command, request["dx"], request["dy"])
    return {
        "command": command,
        "submitted": True,
        "verified": False,
        "coordinates": "relative",
    }


def save_capture(path, png):
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(png)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        os.unlink(path)
        raise


def main():
    runner_values = runner_guard()
    if sys.argv[1:] == ["_worker"]:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise AgentError("worker_request_limit")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise AgentError("invalid_worker_request")
        print(json.dumps({"ok": True, "result": worker(request)}))
        return 0
    # Request-file mode: the whole validated request lives in a JSON file, so no
    # argument (least of all free text) is ever passed through a shell or an
    # environment variable. Capture adds its RAM-disk output path here.
    if sys.argv[1:2] == ["--request"]:
        rest = sys.argv[2:]
        if not rest:
            raise AgentError("request_file_required")
        output = None
        if len(rest) == 3 and rest[1] == "--output":
            output = rest[2]
        elif len(rest) != 1:
            raise AgentError("invalid_request_invocation")
        with open(rest[0], "rb") as handle:
            blob = handle.read(16385)
        if len(blob) > 16384:
            raise AgentError("request_file_limit")
        args = json.loads(blob)
        if not isinstance(args, dict) or not isinstance(args.get("command"), str):
            raise AgentError("invalid_request_file")
        return run_client(args, output, runner_values)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in (
        "status",
        "probe",
        "capture",
        "move",
        "click",
        "wake",
        "tap",
        "swipe",
        "text",
        "key",
        "browse",
    ):
        sub = commands.add_parser(command)
        if command == "browse":
            sub.add_argument("url")
            sub.add_argument("--output", required=True)
            sub.add_argument("--wait-ms", dest="wait_ms", type=int)
        if command == "capture":
            sub.add_argument("--output", required=True)
            sub.add_argument("--expected-package")
        if command in ("move", "click"):
            sub.add_argument("dx", type=int)
            sub.add_argument("dy", type=int)
            sub.add_argument("--expected-package")
        if command == "tap":
            sub.add_argument("x", type=int)
            sub.add_argument("y", type=int)
        if command == "swipe":
            sub.add_argument("x", type=int)
            sub.add_argument("y", type=int)
            sub.add_argument("x2", type=int)
            sub.add_argument("y2", type=int)
            sub.add_argument("--duration-ms", dest="duration_ms", type=int)
        if command == "text":
            sub.add_argument("text")
        if command == "key":
            sub.add_argument("keycode")
    args = vars(parser.parse_args())
    output = args.pop("output", None)
    # Optional arguments left unset carry None; the worker contract omits them.
    args = {key: value for key, value in args.items() if value is not None}
    return run_client(args, output, runner_values)


def run_client(args, output, runner_values):
    """Hand one validated request to the bounded root worker and print its result."""
    if output is not None and os.geteuid() == 0:
        raise AgentError("capture_requires_unprivileged_client")
    script = str(Path(__file__).resolve())
    python = str(Path(sys.executable).resolve())
    assignments = [
        key + "=" + shlex.quote(value) for key, value in runner_values.items()
    ]
    invocation = " ".join(
        assignments
        + [shlex.quote(value) for value in (python, "-I", "-S", script, "_worker")]
    )
    names = ("PATH", "HOME", "TMPDIR", "PREFIX", "LANG")
    environment = {key: os.environ[key] for key in names if key in os.environ}
    environment.update(runner_values)
    result = subprocess.run(
        ["su", "-c", invocation],
        input=json.dumps(args).encode(),
        capture_output=True,
        env=environment,
        timeout=300,
    )
    if len(result.stdout) > 13 * 1024 * 1024:
        raise AgentError("worker_reply_limit")
    response = json.loads(result.stdout)
    if result.returncode or response.get("ok") is not True:
        raise AgentError(response.get("error", "root_worker_failed"))
    metadata = response["result"]
    if output is not None:
        png = base64.b64decode(metadata.pop("png_base64"), validate=True)
        # Keep the worker's own fields (url, wakefulness, focus) next to the PNG's.
        metadata.update(validate_png(png))
        save_capture(output, png)
        metadata["output"] = str(Path(output).absolute())
    print(json.dumps(metadata, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        message = str(error) if isinstance(error, AgentError) else type(error).__name__
        print(json.dumps({"ok": False, "error": message, "verified": False}))
        raise SystemExit(1)
