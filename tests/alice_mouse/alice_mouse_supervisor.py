"""Dry-run transactional supervisor; no device input or process termination."""
from dataclasses import dataclass
from enum import Enum

class Phase(str, Enum):
    LEGACY="legacy"
    QUIESCE="quiesce"
    START_NEW="start_new"
    VERIFY="verify"
    NEW="new"
    ROLLBACK="rollback"

@dataclass
class Supervisor:
    phase: Phase = Phase.LEGACY
    legacy_alive: bool = True
    new_alive: bool = False

    def step(self, event):
        if event == "begin" and self.phase == Phase.LEGACY:
            self.phase = Phase.QUIESCE
        elif event == "legacy_stopped" and self.phase == Phase.QUIESCE:
            self.legacy_alive = False
            self.phase = Phase.START_NEW
        elif event == "new_started" and self.phase == Phase.START_NEW and not self.legacy_alive:
            self.new_alive = True
            self.phase = Phase.VERIFY
        elif event == "healthy" and self.phase == Phase.VERIFY:
            self.phase = Phase.NEW
        elif event in ("timeout","failed") and self.phase in (Phase.QUIESCE,Phase.START_NEW,Phase.VERIFY):
            self.phase = Phase.ROLLBACK
            self.new_alive = False
        elif event == "legacy_restored" and self.phase == Phase.ROLLBACK and not self.new_alive:
            self.legacy_alive = True
            self.phase = Phase.LEGACY
        else:
            raise ValueError("invalid_transition")
        if self.legacy_alive and self.new_alive:
            raise AssertionError("two_mice")
        return self.phase

[executed on device: localhost (bb12d9f4-83a6-41b5-9ede-8717bac12a0a)]
