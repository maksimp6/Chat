"""Lexical command bot for Redmi 9 (Termux + root).

Reads a plain-text Russian command like:
    разблокируй экран с помощью sudo, открой алиса, разбуди

and maps each clause to an Android action:
  разбуди / wake          -> KEYCODE_WAKEUP + Termux wake lock
  разблокируй / unlock    -> KEYCODE_WAKEUP + wm dismiss-keyguard (root; falls
                             back to KEYCODE_MENU swipe when there is no PIN)
  открой <слово> / open   -> resolve a package by name fragment (pm list
                             packages) and start its launcher activity

Runs on the device itself (Termux). Root is used via `su -c` when needed;
on Android that plays the role of sudo. No network, no secrets, read-only
outside the three actions above.

Install (Termux):  pkg install python -y && python redmi_bot.py "разбуди"
Usage:             python redmi_bot.py "разблокируй экран, открой алиса"
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys

WAKEUP = "KEYCODE_WAKEUP"
MENU_UNLOCK = "KEYCODE_MENU"

# Package-name fragments used to resolve an app by a spoken word.
PACKAGE_HINTS: dict[str, tuple[str, ...]] = {
    "алис": ("logos", "yandex"),
    "logos": ("logos", "chat"),
    "яндекс": ("yandex",),
}

CLAUSE_SPLIT = re.compile(r"[,;.!?]|\\bи затем\\b|\\bпотом\\b|\\bзатем\\b", re.IGNORECASE)


def run(
    cmd: list[str], as_root: bool = False, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a command on the device, elevating through su when asked."""
    if as_root:
        argv = ["su", "-c", shlex.join(cmd)]
    else:
        argv = cmd
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    if check and result.returncode != 0:
        print(f"FAIL {' '.join(argv)}: rc={result.returncode} {result.stderr.strip()}")
    return result


def wake() -> bool:
    """Wake the screen."""
    res = run(["input", "keyevent", WAKEUP], as_root=True)
    ok = res.returncode == 0
    lock = run(["termux-wake-lock"], check=False)
    print(f"WAKEUP rc={res.returncode} wake-lock rc={lock.returncode}")
    return ok and lock.returncode == 0


def unlock() -> bool:
    """Wake and dismiss the keyguard (root); fall back to a menu swipe."""
    wake()
    res = run(["wm", "dismiss-keyguard"], as_root=True, check=False)
    if res.returncode == 0:
        print("UNLOCK: dismiss-keyguard ok")
        return True
    res = run(["input", "keyevent", MENU_UNLOCK], as_root=True, check=False)
    print(f"UNLOCK: fallback menu-swipe rc={res.returncode}")
    return res.returncode == 0


def packages() -> list[str]:
    """List installed package names."""
    res = run(["pm", "list", "packages"], as_root=True, check=False)
    return [
        line.split(":", 1)[1].strip()
        for line in res.stdout.splitlines()
        if line.startswith("package:")
    ]


def find_packages(word: str) -> list[str]:
    """Resolve a spoken app word to installed package names."""
    lowered = word.lower().strip()
    hints = PACKAGE_HINTS.get(lowered) or PACKAGE_HINTS.get(lowered[:5])
    fragments = hints or (lowered,)
    found: list[str] = []
    for pkg in packages():
        if any(frag in pkg for frag in fragments):
            found.append(pkg)
    return found


def launch_package(pkg: str) -> bool:
    """Start the launcher activity of a resolved package."""
    res = run(
        ["monkey", "--user", "0", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"],
        as_root=True,
        check=False,
    )
    print(f"OPEN {pkg}: rc={res.returncode}")
    return res.returncode == 0


def open_app(word: str) -> bool:
    """Start an app resolved by a spoken word."""
    found = find_packages(word)
    if not found:
        print(
            f"OPEN: no package matches '{word}' (hints: {PACKAGE_HINTS.get(word.lower().strip())})"
        )
        return False
    if len(found) > 1:
        print(f"OPEN: ambiguous, candidates: {', '.join(found)} — re-run with a package name")
        return False
    return launch_package(found[0])


def execute(clause: str) -> bool:
    """Execute one parsed clause of the owner's command."""
    low = clause.lower().strip()
    if not low:
        return True
    if "разблокир" in low or "unlock" in low:
        return unlock()
    if "разбуди" in low or "wake" in low:
        return wake()
    match = re.match(r"(?:открой|запусти|open|launch)\\s+(.+)", low)
    if match:
        return open_app(match.group(1).strip())
    print(f"SKIP: unknown clause '{clause.strip()}'")
    return True


def main() -> int:
    """CLI entry: python redmi_bot.py \"разблокируй экран, открой алиса, разбуди\"."""
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    clauses = [c for c in CLAUSE_SPLIT.split(sys.argv[1]) if c.strip()]
    results = [execute(c) for c in clauses]
    ok = all(results)
    print("RESULT:", "DONE" if ok else "PARTIAL/FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
