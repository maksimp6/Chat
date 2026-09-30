# Agent implementation handoff

Issue: #627
Parent: #614
Contract PR: #615
Accepted contract head: ebbdbad4122b531ec2a58319232814e08574082e
Stage: implementation
Role: Backend Engineer
Backend: Claude Direct

Contract:
- canonical states are provider-neutral;
- dispatch_created alone => queued;
- non-canonical pending is forbidden;
- workflow in_progress without backend trigger => dispatched;
- terminal precedence: cancelled > blocked > failed > stalled;
- done requires deliverable + successful validation + completed review;
- unknown backend status fails closed.

Constraints:
- tests/test_agent_task_state.py is authoritative;
- do not weaken accepted tests;
- no approval/merge boundary changes.
