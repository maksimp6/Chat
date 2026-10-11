"""Emulate an abrupt keyboard worker crash using a pipe-backed evdev sink.

The pipe is a harmless stand-in for a device descriptor; this deliberately
does NOT claim Android/uinput physical-key release has been verified.
"""
from __future__ import annotations

import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "alice_mouse/drivers/keyboard/keyboard.c"
HEADER = DRIVER.with_name("keyboard.h")


def test_sigkill_closes_emulated_device_fd_without_key_up():
    assert DRIVER.is_file() and HEADER.is_file()
    cc = shutil.which("clang") or shutil.which("cc")
    assert cc, "A C compiler is required for keyboard crash emulation"
    with tempfile.TemporaryDirectory(prefix="alice-key-crash-") as temporary:
        source = Path(temporary) / "crash.c"
        binary = Path(temporary) / "crash"
        source.write_text(
            r"""
#include "keyboard.h"
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
static int out_fd;
static int emit(void *ctx, unsigned short typ, unsigned short code, int value) {
    (void)ctx;
    if (typ == EV_KEY && code == KEY_A) {
        char event = value ? 'D' : 'U';
        return write(out_fd, &event, 1) == 1 ? 0 : -1;
    }
    return 0;
}
static int64_t now_ms(void *ctx) {
    (void)ctx;
    struct timespec time_value;
    clock_gettime(CLOCK_MONOTONIC, &time_value);
    return (int64_t)time_value.tv_sec * 1000 + time_value.tv_nsec / 1000000;
}
int main(int argc, char **argv) {
    if (argc != 2) return 10;
    out_fd = atoi(argv[1]);
    alice_keyboard keyboard;
    alice_keyboard_runner runner;
    if (alice_keyboard_init(&keyboard, NULL, emit, now_ms)) return 11;
    if (alice_keyboard_runner_start(&runner, &keyboard)) return 12;
    if (alice_keyboard_runner_down(&runner, KEY_A)) return 13;
    for (;;) pause(); /* Parent kills ONLY this isolated fixture process. */
}
""",
            encoding="utf-8",
        )
        subprocess.run(
            [
                cc,
                "-std=gnu11",
                "-pthread",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(HEADER.parent),
                str(DRIVER),
                str(source),
                "-o",
                str(binary),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        read_fd, write_fd = os.pipe()
        try:
            worker = subprocess.Popen(
                [str(binary), str(write_fd)],
                pass_fds=(write_fd,),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            os.close(write_fd)
            write_fd = -1
            try:
                ready, _, _ = select.select([read_fd], [], [], 5)
                assert ready, "Worker did not send the simulated KEY_DOWN"
                assert os.read(read_fd, 1) == b"D"
                worker.kill()
                worker.wait(timeout=5)
                assert worker.returncode == -signal.SIGKILL
                # No normal shutdown hook or KEY_UP can run after SIGKILL.
                # The mock descriptor closes on process death (pipe EOF).
                assert os.read(read_fd, 1) == b""
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=5)
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)

