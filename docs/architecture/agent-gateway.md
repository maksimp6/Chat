# Agent Gateway

The Agent Gateway is the provider-neutral lifecycle boundary for Alice Pro agents.

It provides immutable agent descriptors, capability routing, preferred-agent fallback, user authorization and explicit approval for protected agents, timeouts, bounded retries, per-agent rate limits, circuit breaking, normalized invocation results, and optional ExecutionTrace lifecycle events.

Protocol adapters are dependency-free: local Python handlers, JSON REST POST, and A2A JSON-RPC message/send. Authentication is provided at runtime by token providers and is not persisted by the gateway.

The Android application remains thin. Protocol handling, routing, resilience, and policy belong to the backend gateway.

Use the Universal Tool Executor for tool execution. The Agent Gateway is the agent-to-agent boundary; the Universal Tool Executor remains the tool execution boundary.
