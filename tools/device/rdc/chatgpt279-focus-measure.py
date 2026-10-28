"""Sample the ChatGPT Android app over ADB (CPU ticks, frames, jank, window focus, memory) into ~/.logos-rdc/chatgpt279-focus-measure.csv."""

import subprocess, time, re, csv, os

SERIAL = "192.168.31.109:42125"
OUT = os.path.expanduser("~/.logos-rdc/chatgpt279-focus-measure.csv")


def shell(cmd):
    r = subprocess.run(
        ["adb", "-s", SERIAL, "shell", cmd], capture_output=True, text=True, timeout=18
    )
    if r.returncode:
        raise RuntimeError(r.stderr)
    return r.stdout


def snapshot():
    x = shell(
        """p=$(pidof com.openai.chatgpt | awk '{print $1}'); echo PID=$p; awk '{print "TICKS=" $14+$15 " START=" $22}' /proc/$p/stat; echo UPTIME=$(cut -d' ' -f1 /proc/uptime); dumpsys gfxinfo com.openai.chatgpt | grep -E 'Total frames rendered:|Janky frames:' | head -2; dumpsys window | grep mCurrentFocus | head -1; dumpsys meminfo com.openai.chatgpt | grep -E 'TOTAL PSS|TOTAL RSS' | head -2"""
    )

    def m(p):
        return re.search(p, x).group(1)

    return dict(
        pid=int(m(r"PID=(\d+)")),
        ticks=float(m(r"TICKS=([\d.]+)")),
        start=int(m(r"START=(\d+)")),
        up=float(m(r"UPTIME=([\d.]+)")),
        frames=int(m(r"Total frames rendered:\s*(\d+)")),
        jank=int(m(r"Janky frames:\s*(\d+)")),
        focus="com.openai.chatgpt/" in m(r"mCurrentFocus=(.*)"),
        mem=m(r"TOTAL PSS:\s*([\d,]+)"),
    )


with open(OUT, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(
        [
            "run",
            "seconds",
            "cpu_s",
            "avg_cores",
            "frames",
            "janky",
            "jank_pct",
            "focus_all",
            "pid_stable",
            "pss_kb",
            "valid",
        ]
    )
    for i in range(1, 4):
        a = snapshot()
        middle = None
        time.sleep(5)
        middle = snapshot()
        time.sleep(5)
        b = snapshot()
        sec = b["up"] - a["up"]
        cpu = (b["ticks"] - a["ticks"]) / 100
        frames = b["frames"] - a["frames"]
        jank = b["jank"] - a["jank"]
        focus = a["focus"] and middle["focus"] and b["focus"]
        stable = (
            len({a["pid"], middle["pid"], b["pid"]}) == 1
            and len({a["start"], middle["start"], b["start"]}) == 1
        )
        valid = focus and stable and frames >= 0 and jank >= 0 and sec > 0
        row = [
            i,
            round(sec, 2),
            round(cpu, 3),
            round(cpu / sec, 3),
            frames,
            jank,
            round(100 * jank / frames, 2) if frames > 0 else "",
            focus,
            stable,
            b["mem"],
            valid,
        ]
        w.writerow(row)
        f.flush()
        print(row, flush=True)
        if not focus or not stable:
            print("STOP: foreground or process changed", flush=True)
            break
print("SAVED", OUT)
