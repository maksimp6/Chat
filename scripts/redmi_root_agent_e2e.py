#!/usr/bin/env python3
"""Exercise the runner RootAgent against a disposable local browser button."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import struct
import sys
import threading
import time
import zlib
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PAGE = b"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>RootAgent physical input check</title>
<style>html,body{margin:0;height:100%;font:24px sans-serif}button{position:fixed;
inset:0;width:100%;height:100%;border:0;background:#d54438;color:white;font:inherit}
button.done{background:#176c47}</style>
<button id=target type=button>RootAgent test: 0</button>
<script>
const button=document.getElementById('target');let count=0,seq=0,queue=Promise.resolve();
const binding='RUN_BINDING';
function send(data){data.created_ms??=Date.now();data.seq=++seq;queue=queue.then(()=>fetch('/event',{method:'POST',headers:{'Content-Type':'application/json','X-RootAgent-Run':binding},body:JSON.stringify(data)}));return queue}
function heartbeat(){send({kind:'heartbeat',visible:document.visibilityState==='visible',focused:document.hasFocus(),width:innerWidth,height:innerHeight})}
setInterval(heartbeat,100);addEventListener('blur',heartbeat);addEventListener('focus',heartbeat);
button.addEventListener('pointermove',event=>{
 if(event.isTrusted)send({kind:'move',trusted:true,inside:event.target===button,x:event.clientX,y:event.clientY,created_ms:performance.timeOrigin+event.timeStamp});
});
button.addEventListener('pointerleave',event=>{if(event.isTrusted)send({kind:'leave',trusted:true})});
button.addEventListener('click',event=>{
 if(!event.isTrusted){send({kind:'negative',trusted:false});return}
 count++;button.textContent='RootAgent test: '+count;button.className='done';
 send({kind:'click',trusted:true});
 requestAnimationFrame(()=>requestAnimationFrame(()=>send({kind:'paint',trusted:true})));
});
button.click();
function ready(){
 if(innerWidth<=100||innerHeight<=100){requestAnimationFrame(ready);return}
 requestAnimationFrame(()=>{send({kind:'ready',width:innerWidth,height:innerHeight,dpr:devicePixelRatio,background:getComputedStyle(button).backgroundColor});heartbeat()});
}
requestAnimationFrame(ready);
</script>"""


class Witness:
    def __init__(self):
        self.lock = threading.Lock()
        self.ready = False
        self.negative = 0
        self.clicks = 0
        self.move_time = 0.0
        self.move_seq = 0
        self.seq = 0
        self.inside = False
        self.heartbeat_time = 0.0
        self.focused = False
        self.painted = False
        self.page = {}

    def record(self, data):
        with self.lock:
            serial = data.get('seq')
            if type(serial) is not int or serial != self.seq + 1:
                raise ValueError('event_sequence')
            created = data.get('created_ms')
            if type(created) not in (int, float) or not math.isfinite(created):
                raise ValueError('event_timestamp')
            self.seq = serial
            kind = data.get("kind")
            if kind == "ready":
                self.ready = data.get('width', 0) > 100 and data.get('height', 0) > 100
                self.page = {k: data.get(k) for k in ('width', 'height', 'dpr', 'background')}
            elif kind == "negative" and data.get("trusted") is False:
                self.negative += 1
            elif kind == 'heartbeat':
                self.focused = (data.get('visible') is True and data.get('focused') is True
                                and data.get('width', 0) > 100 and data.get('height', 0) > 100)
                self.heartbeat_time = created / 1000
            elif kind == "move" and data.get("trusted") is True:
                self.inside = data.get('inside') is True
                self.move_time = created / 1000
                self.move_seq = serial
            elif kind == 'leave' and data.get('trusted') is True:
                self.inside = False
            elif kind == "click" and data.get("trusted") is True:
                self.clicks += 1
            elif kind == "paint" and data.get("trusted") is True:
                self.painted = True

    def snapshot(self):
        with self.lock:
            return dict(ready=self.ready, negative=self.negative, clicks=self.clicks,
                        move_time=self.move_time, move_seq=self.move_seq, inside=self.inside,
                        heartbeat_time=self.heartbeat_time, focused=self.focused, painted=self.painted,
                        page=dict(self.page))

    def active(self):
        state = self.snapshot()
        return state['focused'] and 0 <= time.time() - state['heartbeat_time'] < 0.6


def wait_for(predicate, seconds, label):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise RuntimeError(label)


def run_cli(helper, *args):
    result = subprocess.run([sys.executable, str(helper), *map(str, args)],
                            capture_output=True, text=True, timeout=30)
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise RuntimeError("ROOTAGENT_INVALID_JSON") from None
    if result.returncode:
        raise RuntimeError("ROOTAGENT_" + str(data.get("error", "FAILED")))
    return data


def wake_display():
    # Reversible test setup only. This does not dismiss Keyguard or enter a PIN.
    result = subprocess.run(['su', '-c', '/system/bin/input keyevent KEYCODE_WAKEUP'],
                            capture_output=True, timeout=15)
    if result.returncode:
        raise RuntimeError('DISPLAY_WAKE_FAILED')


def frame_color(path, target, expected_size=None):
    """Decode PNG scanlines using stdlib only; inspect the visible page area."""
    png = path.read_bytes()
    width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', png[16:29])
    if depth != 8 or color not in (2, 6) or (compression, filtering, interlace) != (0, 0, 0):
        raise RuntimeError('UNSUPPORTED_NATIVE_FRAME')
    if expected_size is not None and (width, height) != expected_size:
        raise RuntimeError('FRAME_SIZE_CHANGED')
    compressed, offset = [], 8
    while offset < len(png):
        size, kind = struct.unpack('>I4s', png[offset:offset + 8])
        if kind == b'IDAT':
            compressed.append(png[offset + 8:offset + 8 + size])
        offset += size + 12
    channels = 3 if color == 2 else 4
    stride = width * channels
    raw = zlib.decompress(b''.join(compressed))
    previous, matched, total = bytearray(stride), 0, 0
    colors = Counter()
    for y in range(height):
        start = y * (stride + 1)
        mode, row = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for x in range(stride):
            a, b = (row[x - channels] if x >= channels else 0), previous[x]
            c = previous[x - channels] if x >= channels else 0
            if mode == 1:
                row[x] = (row[x] + a) & 255
            elif mode == 2:
                row[x] = (row[x] + b) & 255
            elif mode == 3:
                row[x] = (row[x] + (a + b) // 2) & 255
            elif mode == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                row[x] = (row[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        if height // 10 <= y < height * 8 // 10:
            for x in range(width // 10, width * 9 // 10, 4):
                rgb = row[x * channels:x * channels + 3]
                total += 1
                colors[tuple(rgb)] += 1
                matched += all(abs(component - expected) < 35 for component, expected in zip(rgb, target))
        previous = row
    if not total or matched < total * 0.8:
        path.unlink(missing_ok=True)
        raise RuntimeError('NATIVE_TEST_COLOR_NOT_VISIBLE:' + json.dumps({
            'match_ratio': round(matched / max(total, 1), 4),
            'dominant_rgb': colors.most_common(4)}))
    return width, height


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--helper", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package", default="io.github.forkmaintainers.iceraven")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true" or not os.environ.get("GITHUB_RUN_ID"):
        raise SystemExit("NOT_RUN: actual Actions job required")
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    witness = Witness()
    binding = secrets.token_hex(24)
    page = PAGE.replace(b'RUN_BINDING', binding.encode('ascii'))
    page_path = '/' + binding
    origin = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path != page_path or self.headers.get('Host') != origin.removeprefix('http://'):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self):
            if (self.path != "/event" or self.headers.get('Origin') != origin
                    or self.headers.get('Host') != origin.removeprefix('http://')
                    or self.headers.get('Content-Type') != 'application/json'
                    or self.headers.get('X-RootAgent-Run') != binding):
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError()
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError()
                witness.record(data)
            except (ValueError, TypeError):
                self.send_error(400)
                return
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    origin = 'http://127.0.0.1:' + str(server.server_port)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {"run_id": os.environ["GITHUB_RUN_ID"], "source_sha": os.environ.get("GITHUB_SHA"),
              "runner": os.environ.get("RUNNER_NAME"), "result": "FAIL", "input_backend": "existing_root_mouse"}
    try:
        report["probe"] = run_cli(args.helper, "probe")
        wake_display()
        wait_for(lambda: run_cli(args.helper, 'status').get('display_on') is True,
                 8, 'DISPLAY_NOT_AWAKE')
        report["status"] = run_cli(args.helper, "status")
        report['display_setup'] = 'KEYCODE_WAKEUP_WITHOUT_UNLOCK'
        if report["status"].get("keyguard_locked_or_unknown") is not False:
            raise RuntimeError("SCREEN_LOCKED_OR_UNKNOWN")
        url = origin + page_path
        command = " ".join(shlex.quote(x) for x in
                           ["/system/bin/am", "start", "-a", "android.intent.action.VIEW", "-d", url, "-p", args.package])
        opened = subprocess.run(["su", "-c", command], capture_output=True, timeout=15)
        if opened.returncode or b'Error:' in opened.stdout or b'Error:' in opened.stderr:
            raise RuntimeError("TEST_BROWSER_OPEN_FAILED")
        wait_for(lambda: witness.snapshot()["ready"] and witness.snapshot()["negative"] == 1,
                 20, "TEST_PAGE_NOT_READY")
        time.sleep(0.25)
        if witness.snapshot()["clicks"] != 0:
            raise RuntimeError("NEGATIVE_CONTROL_FAILED")
        report["negative_control"] = "PASS_UNTRUSTED_CLICK_IGNORED"
        report['page'] = witness.snapshot()['page']
        wait_for(witness.active, 5, 'OWN_PAGE_NOT_FOCUSED')
        wake_display()
        report["before"] = run_cli(args.helper, "capture", "--expected-package", args.package,
                                   "--output", args.output / "before.png")
        if not witness.active():
            (args.output / 'before.png').unlink(missing_ok=True)
            raise RuntimeError('OWN_PAGE_FOCUS_LOST_DURING_CAPTURE')
        # Only relative cursor movement; no click until the disposable page
        # independently reports a fresh trusted pointer inside its own button.
        width, height = frame_color(args.output / 'before.png', (213, 68, 56))
        for _ in range(math.ceil(max(width, height) / 500) + 2):
            wake_display()
            if not witness.active():
                raise RuntimeError('OWN_PAGE_FOCUS_LOST_BEFORE_MOVE')
            run_cli(args.helper, "move", -500, -500, "--expected-package", args.package)
        previous_move = witness.snapshot()['move_seq']
        wake_display()
        final_move_started = time.time()
        run_cli(args.helper, "move", 200, 350, "--expected-package", args.package)
        wait_for(lambda: witness.active() and witness.snapshot()['inside']
                 and witness.snapshot()['move_seq'] > previous_move
                 and witness.snapshot()['move_time'] >= final_move_started
                 and 0 <= time.time() - witness.snapshot()["move_time"] < 2,
                 3, "TRUSTED_POINTER_NOT_OBSERVED_IN_TEST_BUTTON")
        if not witness.active() or not witness.snapshot()['inside']:
            raise RuntimeError('OWN_BUTTON_NOT_UNDER_POINTER')
        wake_display()
        run_cli(args.helper, "click", 0, 0, "--expected-package", args.package)
        wait_for(lambda: witness.snapshot()["clicks"] == 1 and witness.snapshot()["painted"],
                 5, "TRUSTED_CLICK_NOT_OBSERVED")
        if not witness.active():
            raise RuntimeError('OWN_PAGE_FOCUS_LOST_AFTER_CLICK')
        wake_display()
        report["after"] = run_cli(args.helper, "capture", "--expected-package", args.package,
                                  "--output", args.output / "after.png")
        if not witness.active():
            (args.output / 'after.png').unlink(missing_ok=True)
            raise RuntimeError('OWN_PAGE_FOCUS_LOST_DURING_CAPTURE')
        frame_color(args.output / 'after.png', (23, 108, 71), (width, height))
        before = (args.output / "before.png").read_bytes()
        after = (args.output / "after.png").read_bytes()
        if hashlib.sha256(before).digest() == hashlib.sha256(after).digest():
            raise RuntimeError("NATIVE_FRAME_UNCHANGED")
        report["trusted_clicks"] = witness.snapshot()["clicks"]
        report["native_click"] = "PASS_TRUSTED_BROWSER_EVENT_AND_CHANGED_PNG"
        report["result"] = "PASS"
    except Exception as exc:
        report["error"] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if report['result'] != 'PASS':
            for name in ('before.png', 'after.png'):
                (args.output / name).unlink(missing_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
