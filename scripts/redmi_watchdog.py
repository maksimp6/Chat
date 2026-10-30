"""Keep the phone's Logos answering: a server that stops answering is restarted.

The launcher starts one watchdog beside the server. Every 30 s it asks /healthz; three misses
in a row (no answer within 10 s, or the port refuses) restart the server through
redmi_app_server.sh, which first records the hang (thread stacks in hang-*.txt) and waits for no
running turn. A server stopped on purpose (no backend.pid) is left alone. When a deploy brings a
new version of this file, the running watchdog replaces itself with it.
"""

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CHECK_EVERY_SECONDS = 30
ANSWER_WITHIN_SECONDS = 10
MISSES_BEFORE_RESTART = 3


def log(text):
    print(time.strftime("%Y-%m-%dT%H:%M:%S") + " " + text, flush=True)


def answers(port):
    """True when the server answers at all; an error status is still an answer."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(
            f"http://127.0.0.1:{port}/healthz", timeout=ANSWER_WITHIN_SECONDS
        ) as response:
            response.read(256)
        return True
    except urllib.error.HTTPError:
        return True
    except (OSError, ValueError):
        return False


def main():
    app = Path(os.environ["LOGOS_APP_DIR"]).resolve()
    state = Path(os.environ["LOGOS_STATE_DIR"]).resolve()
    port = int(os.environ.get("LOGOS_PORT", "8080"))
    me = Path(__file__).resolve()
    version = me.stat().st_mtime_ns
    log(f"watchdog started: pid={os.getpid()} port={port}")
    misses = 0
    while True:
        time.sleep(CHECK_EVERY_SECONDS)
        try:
            if me.stat().st_mtime_ns != version:
                log("a new watchdog version was deployed: replacing this process")
                os.execv(sys.executable, [sys.executable, str(me)])
        except OSError:
            pass
        if not (state / "backend.pid").exists():
            misses = 0
            continue
        if answers(port):
            misses = 0
            continue
        misses += 1
        log(f"no answer from /healthz ({misses}/{MISSES_BEFORE_RESTART})")
        if misses < MISSES_BEFORE_RESTART:
            continue
        misses = 0
        log("restarting the server")
        result = subprocess.run(
            ["bash", str(app / "scripts" / "redmi_app_server.sh"), "restart"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log(f"restart exit={result.returncode}: {result.stdout.strip()[-1000:]}")


if __name__ == "__main__":
    main()
