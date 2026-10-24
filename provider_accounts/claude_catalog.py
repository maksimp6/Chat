"""Static Claude capabilities; prices remain unknown until configured."""

from typing import Any

CLAUDE_MODELS: dict[str, dict[str, Any]] = {
    "claude-opus-5-5": {
        "name": "Claude Opus 5.5 (Console OAuth)",
        "provider": "anthropic",
        "auth": "console_oauth",
        "type": "text",
        "capabilities": {"tools": True, "function_calling": True, "multimodal": False},
        "pricing_status": "unknown",
        "currency": "USD",
        "input": None,
        "output": None,
    }
}
