# Vendored from agent_runtime/logos_launch_hold.py. Do not edit this copy: change the canonical file
# and run `python3 scripts/check_independence.py --sync-vendored`.
"""Enforce the owner's court remand before native/model-backed work starts.

There is deliberately no environment, workflow-input or CLI override. Lifting
the hold requires a separately authorized code change after the court's return
conditions have been met. Saved-result readers do not call this guard.
"""

from __future__ import annotations

HOLD_CASE = "https://github.com/maksimp6/Chat/issues/1413#issuecomment-6366889282"
HOLD_REASON = "native_logos_court_remand"


class NativeLogosLaunchBlocked(RuntimeError):
    """A generation or continuation is prohibited by the owner's court hold."""


def enforce_native_launch_hold() -> None:
    """Fail before credentials, a client, tools, or a provider request exist."""
    raise NativeLogosLaunchBlocked(
        f"Native Logos generation and continuation are held by {HOLD_CASE}. "
        "Saved-result reads and deterministic offline checks remain available."
    )


def hold_receipt() -> dict[str, str]:
    return {"status": "held", "reason": HOLD_REASON, "case": HOLD_CASE}


def main() -> int:
    try:
        enforce_native_launch_hold()
    except NativeLogosLaunchBlocked as exc:
        print(str(exc))
        return 1
    return 0  # pragma: no cover -- removal requires separately authorized code review


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
