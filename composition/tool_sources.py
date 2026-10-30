"""Tool sources other units contribute to the shared tool registry.

The registry loads its own sources first, then these, in this order; a loader that
raises the listed exception is logged and skipped, exactly like the registry's own.
"""

from __future__ import annotations


def _government_tools():
    from organization.government import GOVERNMENT_TOOLS

    return GOVERNMENT_TOOLS


def _partner_tools():
    from organization.partner_relations import PARTNER_TOOLS

    return PARTNER_TOOLS


def _printing3d_tools():
    from forge.tools import PRINTING3D_TOOLS

    return PRINTING3D_TOOLS


def _model_editor_tools():
    from model_editor.tools import MODEL_EDITOR_TOOLS

    return MODEL_EDITOR_TOOLS


def _cloud_tools():
    from cloud.tools import CLOUD_TOOLS

    return CLOUD_TOOLS


def _browser_tools():
    from browser.tools import BROWSER_TOOLS

    return BROWSER_TOOLS


def _rootagent_tools():
    from tool_providers.rootagent import ROOTAGENT_TOOLS

    return ROOTAGENT_TOOLS


def _tty_tools():
    from tool_providers.tty import TTY_TOOLS

    return TTY_TOOLS


def _court_tools():
    from court.tools import COURT_TOOLS

    return COURT_TOOLS


def _conversation_tools():
    from conversations.tools import CONVERSATION_TOOLS

    return CONVERSATION_TOOLS


# (category, loader, log label, exception logged instead of raised)
SOURCES = (
    ("government", _government_tools, "Government", Exception),
    ("partner", _partner_tools, "Partner Relations", Exception),
    ("3d", _printing3d_tools, "3D Printing", Exception),
    ("3d", _model_editor_tools, "Model Editor", ImportError),
    ("cloud", _cloud_tools, "Cloud tools", Exception),
    ("browser", _browser_tools, "Browser", Exception),
    ("rootagent", _rootagent_tools, "Root agent", Exception),
    ("tty", _tty_tools, "TTY", Exception),
    ("court", _court_tools, "Court", Exception),
    ("dialog", _conversation_tools, "Dialogs", Exception),
)
