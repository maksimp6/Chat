"""Browser capability, adapter, and orchestration package, and the browser tools."""

from browser.orchestration import (
    BrowserDagBudget,
    BrowserDagResult,
    BrowserRoleDag,
    BrowserRoleTask,
    BrowserTaskBudget,
    BrowserTaskResult,
)

__all__ = [
    "BrowserDagBudget",
    "BrowserDagResult",
    "BrowserRoleDag",
    "BrowserRoleTask",
    "BrowserTaskBudget",
    "BrowserTaskResult",
    "Evidence",
    "MergedEvidence",
    "SemanticDiff",
    "SemanticNode",
    "SemanticSnapshot",
    "build_semantic_snapshot",
    "diff_semantic_snapshots",
    "merge_evidence",
    "BrowserSynthesisPolicy",
    "BrowserSynthesisResult",
    "EscalationDecision",
    "SemanticBrowserWorker",
    "synthesize_browser_dag",
    "BROWSER_TOOLS",
    "BrowserError",
]

from browser.semantic import (
    Evidence,
    MergedEvidence,
    SemanticDiff,
    SemanticNode,
    SemanticSnapshot,
    build_semantic_snapshot,
    diff_semantic_snapshots,
    merge_evidence,
)

from browser.pipeline import (
    BrowserSynthesisPolicy,
    BrowserSynthesisResult,
    EscalationDecision,
    SemanticBrowserWorker,
    synthesize_browser_dag,
)


def __getattr__(name: str):
    """``BROWSER_TOOLS`` and ``BrowserError`` load on first use (no requests import)."""
    if name == "BROWSER_TOOLS":
        from browser.tools import BROWSER_TOOLS

        return BROWSER_TOOLS
    if name == "BrowserError":
        from browser.errors import BrowserError

        return BrowserError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
