# Alice Pro Reasoning and Planning

## Purpose

@Рассуждение is the planning and analysis contour for Alice Pro. It produces explicit structured planning artifacts consumed by an execution controller.

It is not an unrestricted executor and it does not persist a model's private chain-of-thought.

## Architecture

User request -> Router -> @Рассуждение -> Execution Controller -> MCP / Tool Registry / Git / CI -> ExecutionTrace -> validation

A failed step returns to @Рассуждение for replanning.

## Plan contract

The canonical contract is implemented by reasoning_plan.py.

A plan contains:
- goal
- plan_type: analysis, implementation, debug, research, migration, deployment, maintenance
- context
- constraints
- success_criteria
- steps with dependencies
- status
- replan_count
- blocked_reason

Each step contains an id, action, optional target, dependencies, optional step-level success criteria and metadata.

## Lifecycle

1. Collect relevant project context.
2. Analyze the requested outcome and constraints.
3. Create a machine-readable ReasoningPlan.
4. Validate the plan before execution.
5. Execute approved steps in the execution controller.
6. Compare results with explicit success criteria.
7. Replan after a failed step or changed context.
8. Mark completed only when success criteria are explicitly satisfied.

## Execution boundary

@Рассуждение decides what should be done. The execution controller decides how to perform it.

Privileged writes, Git operations, deployment and risky actions remain behind existing authorization and approval boundaries.

The planner must not gain unrestricted shell execution merely because it can create a plan.

## Execution Trace

The planner should emit structured events such as:
- reasoning_started
- context_collected
- plan_created
- plan_validated
- plan_replanned
- plan_completed
- plan_failed

Store explicit plan metadata, validation results, replan reasons and completion state. Do not store private chain-of-thought. Existing trace sanitization remains authoritative.

## Next integration

The autonomous development plugin should consume this contract instead of introducing another planning format. The execution controller should execute one eligible step at a time, record tool results in ExecutionTrace, validate success criteria and request replanning when a step fails.
