"""Observability: Execution Trace (per-invocation diagnostics) and the system log.

- ``observability.trace_manager`` is the primary record of what an invocation did
  (agent steps, API calls, tools, errors, timing), keyed by ``trace_id`` /
  ``invocation_id``.
- ``observability.system_log`` is the small process-level log for startup and
  failures that happen before a trace exists. It is not a second trace.
"""
