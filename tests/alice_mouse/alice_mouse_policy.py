from dataclasses import dataclass

ROLE_ACTIONS = {
    "viewer": frozenset(("status",)),
    "controller": frozenset(("status", "move", "click", "scroll")),
    "admin": frozenset(("status", "move", "click", "scroll", "health")),
}
MOUSE_ACTIONS = frozenset(("move", "click", "scroll"))


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def authorize(role, action, *, foreground, allowed_apps, sensitive=False, confirmed=False):
    if type(role) is not str or type(action) is not str:
        return Decision(False, "invalid")
    if action not in ROLE_ACTIONS.get(role, frozenset()):
        return Decision(False, "role_denied")
    if action in MOUSE_ACTIONS and foreground not in allowed_apps:
        return Decision(False, "app_denied")
    if action in MOUSE_ACTIONS and sensitive and not confirmed:
        return Decision(False, "confirmation_required")
    return Decision(True, "allowed")


def parse_mouse_command(command):
    if type(command) is not dict or set(command) != {"action", "x", "y"}:
        return None
    action, x, y = command["action"], command["x"], command["y"]
    if type(action) is not str or action not in MOUSE_ACTIONS:
        return None
    if type(x) is not int or type(y) is not int:
        return None
    if not (-500 <= x <= 500 and -500 <= y <= 500):
        return None
    if action == "scroll" and y != 0:
        return None
    return action, x, y
