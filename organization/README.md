# `organization`

Departments of the Logos organization: the department registry and API, the Government department (cases and IP registration) and the Partner Relations department (a local CRM with communications and follow-ups), with their tools.

## Public API

`from organization import …`: `departments`, `government`, `partner_relations`.

| Module | Purpose |
| --- | --- |
| [`organization.departments`](departments.py) | First-class Department registry and API for Logos. |
| [`organization.government`](government.py) | Government Department: deterministic case and IP-registration workflow. |
| [`organization.partner_relations`](partner_relations.py) | Partner Relations Department: shared local CRM, communications and follow-up tools. |

## Ports

Everything `organization` uses from other units is a field of `Host` in [`organization/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `TreasuryIdentityError` | `economy.treasury_identity.TreasuryIdentityError` |
| `create_session` | `sessions.manager.create_session` |
| `get_current_owner_id` | `economy.treasury_identity.get_current_owner_id` |
| `get_session` | `sessions.manager.get_session` |
| `get_store` | `storage.records.get_store` |

## Install

- `composition/wiring.py` registers the `organization` host factory (`register_all()`, or `register_units("organization")` for this unit alone); the host is built on first use.
- `composition/web.py` registers the blueprints `partner_relations_bp` (`organization.partner_relations`), `departments_bp` (`organization.departments`), `government_bp` (`organization.government`).
- Storage start-up (`web_shell`) runs `organization.departments.prepare_department_records`, `organization.partner_relations.prepare_partner_relations_records`, `organization.partner_relations.ensure_partner_department`, `organization.government.prepare_government_records`, `organization.government.ensure_government_department`.
- `composition/tool_sources.py` adds the `government` tool category from `organization.government`.
- `composition/tool_sources.py` adds the `partner` tool category from `organization.partner_relations`.
