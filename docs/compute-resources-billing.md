# Computation Resource Tracking and Billing

## Overview

The system now tracks computational resources consumed during request execution and bills them alongside AI token usage. This provides comprehensive cost accounting for all operations performed in the Chat application.

## Tracked Resources

### 1. CPU Time
- **Description**: Wall-clock time spent on processing (excluding external API calls)
- **Unit**: milliseconds (converted to minutes for pricing)
- **Pricing**: 0.05 RUB per minute
- **Calculation**: `processing_time_ms = total_duration_ms - api_time_ms`

### 2. Tool Execution Time
- **Description**: Time spent executing external tools
- **Unit**: milliseconds (converted to minutes for pricing)
- **Pricing**: 0.10 RUB per minute
- **Calculation**: Sum of all `track_tool_execution()` timing_ms values

### 3. API Processing Time
- **Description**: Time spent waiting for API responses
- **Unit**: milliseconds (converted to seconds for pricing)
- **Pricing**: 0.02 RUB per second
- **Calculation**: Sum of all API response `timing_ms` values

## Architecture

### Module: `compute_resources.py`

#### Key Functions

**`extract_timing_metrics(trace: Dict) -> Dict[str, float]`**
- Extracts timing data from an ExecutionTrace
- Returns: `total_duration_ms`, `api_time_ms`, `tool_time_ms`, `processing_time_ms`

**`calculate_compute_cost(metrics: Dict) -> Dict[str, Any]`**
- Calculates costs based on timing metrics and pricing model
- Returns: Individual costs (cpu_cost, tool_cost, api_call_cost) and total_compute_cost

**`build_compute_billing_item(trace: Dict, step: int = 1) -> Dict[str, Any]`**
- Creates a complete billing item for compute resources
- Returns: Billing item with type="compute"

**`aggregate_compute_costs(items: list, context: Optional[Dict]) -> Dict[str, Any]`**
- Aggregates multiple compute items into a summary
- Returns: Aggregate with totals and per-component costs

### Integration with ExecutionTrace

When `ExecutionTrace.finalize()` is called:

1. Timing metrics are extracted from the trace
2. A compute billing item is created automatically
3. Both AI and compute items are aggregated together
4. The combined billing record includes both types of costs

```python
# Example: Automatic compute tracking in ExecutionTrace
trace = ExecutionTrace(trace_id="trace-123")
trace.set_context(user_id="user-1")

# Regular operations tracked by ExecutionTrace
trace.add_response({...})
trace.track_tool_execution(...)

# On finalize, compute resources are automatically calculated
finalized = trace.finalize()

# Billing includes both AI and compute costs
billing = finalized["billing"]
# billing["total_cost"] = ai_cost + compute_cost
# billing["compute_cost"] = total compute resource cost
# billing["items"] = [ai_item, compute_item]
```

### Integration with Treasury Billing

Compute costs are settled to the treasury the same way as AI costs:

```python
from billing import settle_billing_to_treasury

# The total_cost includes both AI and compute costs
result = settle_billing_to_treasury(billing, owner_id)
```

## Billing Output Structure

```json
{
  "type": "compute",
  "step": 1,
  "provider": "alice_compute",
  "currency": "RUB",
  "pricing_version": "compute-v1",
  "cost_status": "calculated",
  "cpu_time_ms": 500,
  "cpu_cost": 0.05,
  "tool_time_ms": 300,
  "tool_cost": 0.05,
  "api_time_ms": 1000,
  "api_call_cost": 0.02,
  "total_compute_cost": 0.12,
  "total_cost": 0.12
}
```

## Aggregate Billing Output

```json
{
  "total_cost": 0.25,
  "compute_cost": 0.12,
  "tool_cost": 0.05,
  "api_call_cost": 0.02,
  "cpu_cost": 0.05,
  "cpu_time_ms": 500,
  "tool_time_ms": 300,
  "api_time_ms": 1000
}
```

## Pricing Configuration

Pricing is defined in `compute_resources.py`:

```python
COMPUTE_PRICING = {
    "cpu_time_per_minute": 0.05,      # RUB per minute of CPU time
    "tool_execution_per_minute": 0.10,  # RUB per minute of tool time
    "api_call_per_second": 0.02,       # RUB per second of API time
}
```

To adjust pricing, modify these constants and re-deploy.

## Cost Calculation Examples

### Example 1: Simple Chat Request
- Total duration: 2 seconds
- API response time: 1.5 seconds
- Tool execution: 0 seconds
- CPU time: 0.5 seconds = 0.008 minutes
- CPU cost: 0.008 × 0.05 = 0.0004 RUB
- API cost: 1.5 × 0.02 = 0.03 RUB
- **Total compute cost: 0.0304 RUB**

### Example 2: Multi-Tool Orchestration
- Total duration: 10 seconds
- API response time: 2 seconds
- Tool execution: 6 seconds = 0.1 minutes
- CPU time: 2 seconds = 0.033 minutes
- CPU cost: 0.033 × 0.05 = 0.00166 RUB
- Tool cost: 0.1 × 0.10 = 0.01 RUB
- API cost: 2 × 0.02 = 0.04 RUB
- **Total compute cost: 0.05166 RUB**

## Testing

Comprehensive test suite included:

- `test_compute_resources.py`: Unit tests for cost calculation
- `test_trace_compute_integration.py`: Integration tests with ExecutionTrace

Run tests:
```bash
python -m pytest tests/test_compute_resources.py tests/test_trace_compute_integration.py -v
```

## Backwards Compatibility

- Existing billing tests pass without modification
- AI token billing remains unchanged
- Compute costs are additive to AI costs
- The treasury system handles combined costs transparently

## Future Enhancements

Potential improvements:

1. **Variable Pricing**: Different pricing for different tool types
2. **Resource Monitoring**: Track memory and CPU usage from OS
3. **Cost Attribution**: Break down costs by user operation type
4. **Optimization Alerts**: Flag inefficient patterns
5. **Budgeting**: Set spending limits per user/session
