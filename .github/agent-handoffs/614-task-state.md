# Agent implementation handoff

Issue: #627
Parent: #614
Contract PR: #615
Accepted contract head: 932a0541bfafdc75868fdac4924d23d5f9c80b6e
Stage: implementation
Role: Backend Engineer
Backend: Claude Direct

Contract:
- backend-neutral canonical task states;
- mentioned/subscribed alone never proves working;
- workflow in_progress without backend trigger remains dispatched;
- terminal precedence: cancelled > blocked > failed > stalled;
- done requires deliverable + successful validation + completed review;
- unknown backend status fails closed.

Constraints:
- accepted tests/test_agent_task_state.py is authoritative and must not be weakened;
- implement only agent_office/task_state.py plus minimal supporting production code if strictly necessary;
- no provider-specific state semantics;
- no approval or merge boundary changes.
