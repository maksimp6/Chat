# `browser`

Browser capabilities: contracts shared by the cloud and local browsing adapters, an adapter backed by the Node.js browser emulator, deterministic parallel browser-role orchestration and semantic synthesis of its results.

## Public API

`from browser import …`: `BrowserDagBudget`, `BrowserDagResult`, `BrowserRoleDag`, `BrowserRoleTask`, `BrowserTaskBudget`, `BrowserTaskResult`, `Evidence`, `MergedEvidence`, `SemanticDiff`, `SemanticNode`, `SemanticSnapshot`, `build_semantic_snapshot`, `diff_semantic_snapshots`, `merge_evidence`, `BrowserSynthesisPolicy`, `BrowserSynthesisResult`, `EscalationDecision`, `SemanticBrowserWorker`, `synthesize_browser_dag`.

| Module | Purpose |
| --- | --- |
| [`browser.adapters`](adapters.py) | Adapter boundary for Logos's cloud and local browser capabilities. |
| [`browser.capabilities`](capabilities.py) | Contracts shared by Logos's cloud and local browsing adapters. |
| [`browser.emulator_adapter`](emulator_adapter.py) | Browser adapter backed by the lightweight Node.js browser emulator. |
| [`browser.orchestration`](orchestration.py) | Deterministic parallel browser-role DAG orchestration. |
| [`browser.pipeline`](pipeline.py) | Semantic synthesis and cheap-first escalation for BrowserRoleDag results. |
| [`browser.semantic`](semantic.py) | Token-efficient semantic browser state and evidence contracts. |

## Ports

None. The unit imports only the standard library, third-party packages and its own modules.

## Install

- Nothing to wire: import the modules you need.
