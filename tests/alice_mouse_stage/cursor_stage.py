"""Preview-only state adapter for a verified Alice Mouse C event.

This module cannot inject touch/mouse events. Only the authenticated HTTP->C
verifier should invoke consume() with its accepted event. Renderer callbacks
must remain unprivileged visual-only operations.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

_EVENT = re.compile(r"EVENT ([123]) ([12]) (-?[0-9]{1,3}) (-?[0-9]{1,3})\Z")


@dataclass
class PreviewState:
    x: int
    y: int
    button: int | None = None


class CursorPreviewAdapter:
    def __init__(self, width: int, height: int,
                 show: Callable[[int, int], None],
                 hide: Callable[[], None]):
        if type(width) is not int or type(height) is not int or not 48 <= width <= 8192 or not 48 <= height <= 8192:
            raise ValueError("invalid display size")
        self.width, self.height = width, height
        self.show, self.hide = show, hide
        self.state = PreviewState(width // 2, height // 2)
        self.last_rotation = 0

    def consume(self, verified_c_event: str) -> bool:
        if not isinstance(verified_c_event, str):
            return False
        match = _EVENT.fullmatch(verified_c_event)
        if match is None:
            return False
        action, button, dx, dy = map(int, match.groups())
        if not (-500 <= dx <= 500 and -500 <= dy <= 500):
            return False
        if action == 2:
            if self.state.button is not None:
                return False
            self.state.button = button
        elif action == 3:
            if self.state.button != button:
                return False
            self.state.button = None
        elif action == 1:
            self.state.x = min(self.width - 1, max(0, self.state.x + dx))
            self.state.y = min(self.height - 1, max(0, self.state.y + dy))
        self.show(self.state.x, self.state.y)
        return True

    def display_changed(self, width: int, height: int, rotation: int) -> bool:
        if (type(width) is not int or type(height) is not int or
                type(rotation) is not int or not 48 <= width <= 8192 or
                not 48 <= height <= 8192 or rotation not in (0, 1, 2, 3)):
            return False
        if (width, height, rotation) != (self.width, self.height, self.last_rotation):
            self.hide()
            self.state = PreviewState(width // 2, height // 2)
            self.width, self.height, self.last_rotation = width, height, rotation
        return True

    def disconnect(self) -> None:
        self.hide()
        self.state.button = None
