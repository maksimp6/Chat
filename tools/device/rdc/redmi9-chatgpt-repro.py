#!/usr/bin/env python3
"""Reproduce the Redmi 9 ChatGPT app measurement over ADB (CPU ticks, frames, jank, window focus) into ~/.logos-rdc/redmi9-chatgpt-repro-20261011.csv."""

import subprocess, time, re, csv, os

serial = "192.168.31.29:35087"
out = os.path.expanduser("~/.logos-rdc/redmi9-chatgpt-repro-20261011.csv")


def sh(s):
    r = subprocess.run(
        ["adb", "-s", serial, "shell", s], capture_output=True, text=True, timeout=12
    )
    if r.returncode:
        raise RuntimeError(r.stderr[:200])
    return r.stdout


def snap():
    x = sh(
        "p=$(pidof com.openai.chatgpt | awk '{print $1}'); echo PID=$p; awk '{print \"TICKS=\" $14+$15 \" STARTTIME=\" $22}' /proc/$p/stat; cat /proc/uptime | cut -d' ' -f1; dumpsys gfxinfo com.openai.chatgpt | grep -E 'Total frames rendered:|Janky frames:' | head -2; dumpsys window | grep mCurrentFocus | head -1"
    )

    def one(pattern):
        return re.search(pattern, x).group(1)

    return dict(
        pid=int(one(r"PID=(\d+)")),
        ticks=float(one(r"TICKS=([0-9.]+)")),
        starttime=int(one(r"STARTTIME=(\d+)")),
        uptime=float(one(r"(?m)^([0-9]+\.[0-9]+)$")),
        frames=int(one(r"Total frames rendered:\s*(\d+)")),
        jank=int(one(r"Janky frames:\s*(\d+)")),
        foreground="com.openai.chatgpt" in one(r"mCurrentFocus=(.*)"),
    )


with open(out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(
        [
            "run",
            "seconds",
            "cpu_s",
            "avg_cores",
            "frames",
            "jank",
            "jank_percent",
            "focus_both",
            "pid_stable",
        ]
    )
    for i in range(1, 4):
        a = snap()
        time.sleep(15)
        b = snap()
        sec = b["uptime"] - a["uptime"]
        cpu = (b["ticks"] - a["ticks"]) / 100
        frames = b["frames"] - a["frames"]
        jank = b["jank"] - a["jank"]
        stable = a["pid"] == b["pid"] and a["starttime"] == b["starttime"]
        if not stable or frames < 0 or jank < 0 or sec <= 0:
            raise RuntimeError("counter reset / PID change")
        row = [
            i,
            round(sec, 2),
            round(cpu, 3),
            round(cpu / sec, 3),
            frames,
            jank,
            round(100 * jank / frames, 2) if frames else None,
            a["foreground"] and b["foreground"],
            stable,
        ]
        w.writerow(row)
        f.flush()
        print(row, flush=True)
print("SAVED", out)
