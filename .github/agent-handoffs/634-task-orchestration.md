# Agent implementation handoff

Issue: #636
Parent: #634 / #576
Contract PR: #635
Accepted contract head: b07e1100479f9432ddfb4f9b9c6dc5838d28bc46
Stage: implementation
Role: Backend Engineer
Backend: Claude Direct

Contract:
- TaskPacket identity is immutable;
- BriefOpinion is advisory only;
- one non-terminal authoritative task per work item;
- continuation of same task_id is idempotent;
- retry after failed/stalled requires genuinely new EvidenceRef;
- accepted contract provenance is required from implementation onward;
- base_ref is a git ref and need not equal contract SHA;
- TaskRegistry admission is coupled to next_stage_after;
- lifecycle:
  contract + RED_READY -> contract-review
  contract-review + ACCEPTED -> implementation
  implementation + success -> verification
  verification + success -> solution-review
  solution-review + ACCEPTED -> maintain
  maintain + READY -> protected merge;
- CHANGES_REQUESTED -> implementation -> verification -> fresh solution-review;
- material_outcome is separate from canonical task state;
- reuse task_state and dispatch_model review gates.

Constraints:
- do not weaken tests/test_task_orchestration_contract.py;
- do not expand approval/merge authority;
- do not duplicate existing review gate logic.
