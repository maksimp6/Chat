# Energy-based compute billing

Every `ExecutionTrace` records how much local compute the invocation actually
consumed and converts it to energy and, optionally, money.

## Formula

```
cpu_seconds = billable_cpu_ms / 1000   # thread CPU at the last recorded work
energy_J    = cpu_seconds * ALICE_CPU_WATTS_PER_CORE
energy_Wh   = energy_J / 3600
cost_RUB    = energy_Wh / 1000 * ALICE_ELECTRICITY_PRICE_RUB_PER_KWH
```

## Billable boundary

Every `add_event()` on the thread that owns the trace (all mutation APIs end
with one) records `timings.billable_cpu_ms` and `timings.billable_wall_ms` as
source facts. `make_snapshot()` and `finalize()` derive the compute item from
these facts inside `_build_snapshot()` and never append it to the live trace.
Reading a snapshot or finalizing is therefore not billed as work: if no work
is recorded between them, `snapshot["billing"] == finalized["billing"]`.

Wall-clock time is recorded (`wall_seconds`, `cpu_utilization`) but never
billed: time spent waiting for Yandex AI Studio, the network or `sleep` uses no
local CPU, and the provider's own compute is already billed through tokens.

## Configuration

| Variable                              | Default | Meaning                                                                                                                            |
| ------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `ALICE_CPU_WATTS_PER_CORE`            | `15`    | Power of one fully busy core, W. Server core ≈ 10–20 W, phone (Termux) ≈ 2–5 W. Include cooling/PSU overhead (PUE) here if needed. |
| `ALICE_ELECTRICITY_PRICE_RUB_PER_KWH` | unset   | Electricity price. When unset, energy is recorded with `cost_status: not_billed` and nothing is charged.                           |

Invalid, negative or non-finite values are ignored with a warning.

## Trace output

`billing.items[]` gets one `type: "compute"` item per trace:

```json
{
  "type": "compute",
  "pricing_version": "energy-v1",
  "cpu_seconds": 0.042,
  "wall_seconds": 3.1,
  "cpu_utilization": 0.0135,
  "watts_per_core": 15.0,
  "energy_joules": 0.63,
  "energy_wh": 0.000175,
  "price_per_kwh": 6.5,
  "cost_status": "calculated",
  "total_cost": 0.000001
}
```

The aggregate `billing` adds `cpu_seconds`, `energy_wh` and `compute_cost`
(already included in `total_cost`). Each `tool_calls[]` entry has `cpu_ms`.

## Guarantees and limits

- Exactly one compute item per snapshot; repeated snapshots and `finalize()`
  calls do not add items or change billing (see `docs/execution-trace-lifecycle.md`).
- A `not_billed` compute item never turns AI billing into `partial`, so token
  settlement to Treasury is unaffected.
- `thread_time` is per thread. Work recorded on another thread is not
  attributed; if nothing was recorded on the owning thread, the compute item is
  `not_billed` with `cost_reason: cpu_time_unmeasured` instead of a guess.
- CPU spent after the last recorded work (e.g. serializing the response) is
  not billed.
- If building the compute item fails, it is reported as `not_billed` with
  `cost_reason: compute_billing_failed`; the trace is not mutated.
- CPU used by subprocesses or by threads a tool spawns is not included.
- This is the marginal (dynamic) energy of the request. The server's idle power,
  hardware and hosting rent are fixed costs and not attributed per request.
