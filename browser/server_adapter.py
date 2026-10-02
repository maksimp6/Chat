"""Opt-in transport adapter for the private server worker."""

import json

from browser.capabilities import BrowserAction


class ServerBrowserAdapter:
    """Transport runs the fixed worker command via the approved SSH runtime.

    The caller supplies a runtime-scoped transport, not a model-chosen shell
    command or hostname. UniversalToolExecutor retains invocation/trace ownership.
    """

    def __init__(self, transport):
        self.transport = transport

    def execute(self, action: BrowserAction):
        if action.action not in {"navigate", "inspect", "assert_state"}:
            return {"success": False, "error": "interactive_channel_not_configured"}
        try:
            raw = self.transport(json.dumps(action.to_arguments()).encode())
            if len(raw) > 16384:
                raise ValueError("oversized_result")
            result = json.loads(raw)
            if not isinstance(result, dict) or not isinstance(result.get("success"), bool):
                raise ValueError("invalid_result")
            # Drop every unrecognized field rather than forwarding transport data.
            data = result.get("data") or {}
            return {
                "success": result["success"],
                "data": {key: data[key] for key in ("title", "origin") if key in data},
                "error": None if result["success"] else "server_browser_failed",
            }
        except Exception:
            return {"success": False, "error": "server_browser_transport_failed"}
