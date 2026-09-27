# Energy-based compute billing

Every `ExecutionTrace` records how much local compute the invocation actually
consumed and converts it to energy and, optionally, money.

## Formula

```
cpu_seconds = time.thread_time() at finalize - time.thread_time() at trace start
energy_J    = cpu_seconds * ALICE_CPU_WATTS_PER_CORE
energy_Wh   = energy_J / 3600
cost_RUB    = energy_Wh / 1000 * ALICE_ELECTRICITY_PRICE_RUB_PER_KWH
```

Wall-clock time is recorded (`wall_seconds`, `cpu_utilization`) but never
billed: time spent waiting for Yandex AI Studio, the network or `sleep` uses no
local CPU, and the provider's own compute is already billed through tokens.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALICE_CPU_WATTS_PER_CORE` | `15` | Power of one fully busy core, W. Server core ≈ 10–20 W, phone (Termux) ≈ 2–5 W. Include cooling/PSU overhead (PUE) here if needed. |
| `ALICE_ELECTRICITY_PRICE_RUB_PER_KWH` | unset | Electricity price. When unset, energy is recorded with `cost_status: not_billed` and nothing is charged. |

Invalid, negative or non-finite values are ignored with a warning.

## Trace output

`billing.items[]` gets one `type: "compute"` item per trace:

```json
{"type": "compute", "pricing_version": "energy-v1", "cpu_seconds": 0.042,
 "wall_seconds": 3.1, "cpu_utilization": 0.0135, "watts_per_core": 15.0,
 "energy_joules": 0.63, "energy_wh": 0.000175, "price_per_kwh": 6.5,
 "cost_status": "calculated", "total_cost": 0.000001}
```

The aggregate `billing` adds `cpu_seconds`, `energy_wh` and `compute_cost`
(already included in `total_cost`). Each `tool_calls[]` entry has `cpu_ms`.

## Guarantees and limits

- Compute is measured once; repeated `finalize()` calls do not add new items.
- A `not_billed` compute item never turns AI billing into `partial`, so token
  settlement to Treasury is unaffected.
- `thread_time` is per thread. If a trace is finalized on a different thread
  than it was created on, CPU time is reported as unmeasured
  (`cost_reason: cpu_time_unmeasured`) instead of guessing.
- CPU used by subprocesses or by threads a tool spawns is not included.
- This is the marginal (dynamic) energy of the request. The server's idle power,
  hardware and hosting rent are fixed costs and not attributed per request.
