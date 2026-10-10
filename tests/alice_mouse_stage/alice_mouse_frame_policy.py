"""Frame-aware Alice Mouse control policy; isolated from live root daemon.

A frame proves observation, not authorization. Heartbeat requires the controller
secret and monotonic sequence. A hard hold deadline prevents indefinite drag.
"""
from __future__ import annotations
import hmac
import secrets
import time
from dataclasses import dataclass

@dataclass(frozen=True)
class Frame:
    sequence: int
    capture_end_ns: int
    frame_id: str

class FrameAwareMouse:
    def __init__(self, *, now_ns=None, freshness_ms=2000, heartbeat_ms=1000,
                 max_hold_ms=5000):
        self.clock=now_ns or time.monotonic_ns
        self.freshness_ns=freshness_ms*1_000_000
        self.heartbeat_ns=heartbeat_ms*1_000_000
        self.max_hold_ns=max_hold_ms*1_000_000
        self.token=secrets.token_hex(32)
        self.session_active=True
        self.heartbeat_seq=-1
        self.heartbeat_at=-1
        self.frame=None
        self.button=None
        self.pressed_at=None
        self.events=[]
    def heartbeat(self, token, sequence):
        if not self.session_active or not hmac.compare_digest(str(token),self.token):
            return False
        if type(sequence) is not int or sequence<=self.heartbeat_seq:
            return False
        self.heartbeat_seq=sequence
        self.heartbeat_at=self.clock()
        return True
    def observe(self, frame:Frame):
        now=self.clock()
        if type(frame.sequence) is not int or type(frame.capture_end_ns) is not int:
            return False
        if frame.capture_end_ns>now or now-frame.capture_end_ns>self.freshness_ns:
            return False
        if self.frame and (frame.sequence<=self.frame.sequence or
                           frame.capture_end_ns<self.frame.capture_end_ns):
            return False
        self.frame=frame
        return True
    def _controller_live(self):
        return self.session_active and self.heartbeat_at>=0 and (
            0<=self.clock()-self.heartbeat_at<=self.heartbeat_ns)
    def _frame_fresh(self):
        return self.frame is not None and (
            0<=self.clock()-self.frame.capture_end_ns<=self.freshness_ns)
    def tick(self):
        if self.button is not None and (
            not self._controller_live() or
            self.clock()-self.pressed_at>=self.max_hold_ns):
            self.events.append(("up",self.button,"safety"))
            self.button=None
            self.pressed_at=None
    def command(self, action, *, button=1):
        self.tick()
        if action=="up":
            if self.button!=button:return False
            self.events.append(("up",button,"controller"))
            self.button=None;self.pressed_at=None
            return True
        if not self._controller_live() or not self._frame_fresh():
            return False
        if action=="down":
            if self.button is not None or button not in (1,2):return False
            self.button=button;self.pressed_at=self.clock()
        elif action!="move":
            return False
        self.events.append((action,button,"controller"))
        return True
    def disconnect(self):
        self.session_active=False
        self.tick()
