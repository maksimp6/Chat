"""Browser capability, adapter, and orchestration package."""

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
