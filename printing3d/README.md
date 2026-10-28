# `printing3d`

The 3D-printing business domain, independent of the host app (quotes and finance, slicing, model preflight, G-code audit, Bambu A1 mini telemetry and delivery, the print workflow, video), and `forge`, which binds it to Logos storage and identity and serves its HTTP API and tools. `printing3d` never imports `forge`.

## Public API

`from forge import …`: `init_printing_tables`, `printing_store`.

| Module | Purpose |
| --- | --- |
| [`forge.binding`](../forge/binding.py) | Binds the 3D-printing domain to Logos storage and identity. |
| [`forge.routes`](../forge/routes.py) | HTTP API for the Logos 3D-printing business MVP. |
| [`forge.tools`](../forge/tools.py) | Universal local tools for the Logos 3D-printing business domain. |

`from printing3d import …`: `ORDER_STATUSES`, `PrintStore`, `assess_financing`, `calculate_quote`, `create_order`, `get_financial_summary`, `get_financing_plan`, `get_order`, `get_payback_status`, `list_orders`, `set_financing_plan`, `settle_order`, `update_order_status`.

| Module | Purpose |
| --- | --- |
| [`printing3d.plate`](plate.py) | Bounded read-only preflight for a single sliced Bambu A1 mini .gcode.3mf. |
| [`printing3d.shape_benchmarks`](shape_benchmarks.py) | Deterministic, printer-agnostic FDM geometry test coupons (no printer I/O). |
| [`printing3d.slicer`](slicer.py) | Dependency-free STL slicer for simple printable parts. |
| [`printing3d.video`](video/__init__.py) | Deterministic 3D video storyboard and rendering helpers. |

Command line:

- `python -m printing3d.plate`
- `python -m printing3d.shape_benchmarks`
- `python -m printing3d.slicer`
- `python -m printing3d.video`

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `forge.binding.printing_store` | `printing3d` |

## Ports

Everything `forge` uses from other units is a field of `Host` in [`forge/host.py`](../forge/host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `get_current_owner_id` | `economy.treasury_identity.get_current_owner_id` |
| `get_store` | `storage.db.get_store` |

Everything `printing3d` uses from other units is a field of `Host` in [`printing3d/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `printing_store` | `forge.binding.printing_store` |

## Install

- `composition/wiring.py` registers the `forge` host factory (`register_all()`, or `register_units("forge")` for this unit alone); the host is built on first use.
- `composition/wiring.py` registers the `printing3d` host factory (`register_all()`, or `register_units("printing3d")` for this unit alone); the host is built on first use.
- `composition/web.py` registers the blueprint `printing3d_bp` (`forge.routes`).
- Storage start-up (`web_shell`) runs `forge.init_printing_tables`.
- `composition/tool_sources.py` adds the `3d` tool category from `forge.tools`.
