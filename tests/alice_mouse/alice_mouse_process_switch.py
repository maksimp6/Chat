"""Transactional process cutover, tested with harmless processes.

This module does NOT install or modify Magisk by itself. A separate
watchdog/boot coordination gate is mandatory for real mouse cutover.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class SwitchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SwitchConfig:
    target: Path
    candidate: Path
    backup: Path
    old_pid: int
    lock: Path
    journal: Path
    pending: Path | None = None
    rollback_binary: Path | None = None
    timeout: float = 2.0
    args: tuple[str, ...] = ("serve",)


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as file:
            state = file.read().rsplit(")", 1)[1].strip().split()[0]
            if state == "Z":
                return False
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ProcessLookupError):
        return False


def wait_dead(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.025)
    return not alive(pid)


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def atomic_install(source: Path, target: Path) -> None:
    """Copy + fsync + atomic rename on the target filesystem."""
    temp = target.with_name(target.name + f".pending.{os.getpid()}")
    try:
        with source.open("rb") as inp, temp.open("xb") as out:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp, 0o700)
        os.replace(temp, target)
        directory = os.open(str(target.parent), os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


def journal_write(path: Path, state: str) -> None:
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temp.open("w", encoding="ascii") as out:
        out.write(state + "\n")
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)
    directory = os.open(str(path.parent), os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def alice_mouse_device_active() -> bool:
    """Return true if registered, or if device enumeration is unavailable."""
    try:
        return 'Name="Alice RDC Virtual Mouse"' in Path("/proc/bus/input/devices").read_text()
    except OSError:
        return True


class ProcessSwitch:
    """Real process operations with synchronous failure rollback.

    This does not protect against supervisor SIGKILL or a reboot; do not
    use for production until a cooperative boot watcher is installed.
    """

    def __init__(self, cfg: SwitchConfig, health: Callable[[int], bool] | None = None,
                 external_mouse_active: Callable[[], bool] | None = None):
        self.cfg = cfg
        self.health = health or alive
        self.external_mouse_active = external_mouse_active or alice_mouse_device_active
        self.new_pid: int | None = None
        self.restored_pid: int | None = None
        self.children: dict[int, subprocess.Popen] = {}

    def _spawn(self) -> int:
        process = subprocess.Popen(
            [str(self.cfg.target), *self.cfg.args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        self.children[process.pid] = process
        return process.pid

    def _healthy(self, pid: int) -> bool:
        started = time.monotonic()
        deadline = started + self.cfg.timeout
        stable_until = started + min(0.35, self.cfg.timeout * 0.5)
        while time.monotonic() < deadline:
            if not alive(pid):
                return False
            if time.monotonic() >= stable_until and self.health(pid):
                return True
            time.sleep(0.03)
        return alive(pid) and self.health(pid) and time.monotonic() >= stable_until

    def _stop(self, pid: int) -> None:
        if not alive(pid):
            return
        os.kill(pid, signal.SIGTERM)
        if not wait_dead(pid, self.cfg.timeout):
            raise SwitchError("process_did_not_stop")
        child = self.children.get(pid)
        if child is not None:
            child.wait(timeout=0.3)

    def _restore(self, original_still_alive: bool) -> None:
        if self.new_pid is not None and alive(self.new_pid):
            self._stop(self.new_pid)
        # Never restore/start the unguarded original while candidate still lives.
        if self.new_pid is not None and alive(self.new_pid):
            raise SwitchError("rollback_refuses_two_instances")
        if not original_still_alive:
            deadline = time.monotonic() + self.cfg.timeout
            while self.external_mouse_active() and time.monotonic() < deadline:
                time.sleep(0.05)
            if self.external_mouse_active():
                raise SwitchError("rollback_refuses_untracked_mouse")
        atomic_install(self.cfg.rollback_binary or self.cfg.backup, self.cfg.target)
        if not original_still_alive and not alive(self.cfg.old_pid):
            self.restored_pid = self._spawn()
            if not self._healthy(self.restored_pid):
                raise SwitchError("rollback_restart_failed")

    def run(self, *, fault: str | None = None) -> int:
        cfg = self.cfg
        cfg.lock.parent.mkdir(parents=True, exist_ok=True)
        with cfg.lock.open("a+b") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SwitchError("another_transaction_running") from exc
            if cfg.old_pid == os.getpid() or not alive(cfg.old_pid):
                raise SwitchError("missing_original_process")
            if not (cfg.target.is_file() and cfg.backup.is_file() and cfg.candidate.is_file()):
                raise SwitchError("missing_required_files")
            if cfg.rollback_binary is not None and not cfg.rollback_binary.is_file():
                raise SwitchError("missing_rollback_guard")
            if digest(cfg.target) != digest(cfg.backup):
                raise SwitchError("original_backup_mismatch")
            journal_write(cfg.journal, "PREPARING")
            if cfg.pending is not None:
                boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                journal_write(cfg.pending, boot_id)
            try:
                atomic_install(cfg.candidate, cfg.target)
                journal_write(cfg.journal, "CANDIDATE_INSTALLED")
                if fault == "after_install":
                    raise SwitchError("fault_after_install")
                self._stop(cfg.old_pid)
                journal_write(cfg.journal, "OLD_STOPPED")
                deadline = time.monotonic() + cfg.timeout
                while self.external_mouse_active() and time.monotonic() < deadline:
                    time.sleep(0.05)
                if self.external_mouse_active():
                    raise SwitchError("untracked_mouse_after_stop")
                if fault == "after_stop":
                    raise SwitchError("fault_after_stop")
                self.new_pid = self._spawn()
                journal_write(cfg.journal, "NEW_SPAWNED")
                if fault == "after_spawn":
                    raise SwitchError("fault_after_spawn")
                if not self._healthy(self.new_pid):
                    raise SwitchError("candidate_unhealthy")
                journal_write(cfg.journal, "COMMITTED")
                if cfg.pending is not None:
                    cfg.pending.unlink(missing_ok=True)
                return self.new_pid
            except BaseException:
                try:
                    self._restore(original_still_alive=alive(cfg.old_pid))
                    journal_write(cfg.journal, "ROLLED_BACK")
                    if cfg.pending is not None:
                        cfg.pending.unlink(missing_ok=True)
                except BaseException:
                    journal_write(cfg.journal, "MANUAL_RECOVERY_REQUIRED")
                    raise
                raise
