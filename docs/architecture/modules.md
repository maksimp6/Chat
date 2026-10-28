# Modules and their independence

Logos is built from independent **units**: every top-level package, every file under
`scripts/` and `tools/`, and the script clusters declared in `pyproject.toml`
(`[tool.logos.units]`). The target is that no unit imports another one. Units get what
they need from outside through **ports**, and only the **composition roots** (`app.py`
and, as the refactor proceeds, the `composition/` package and declared CLI entry
blocks) wire units together.

## Rules

1. A unit imports only the standard library, the third-party packages from
   `requirements*.txt` and its own modules. Lazy imports, `importlib` strings and
   embedded `-c` programs count too.
2. Composition roots only construct and wire objects; they hold no logic, and no unit
   imports them.
3. A unit declares what it needs in its own `ports.py` (`typing.Protocol`) and receives
   implementations from a root. There is no shared "contracts" package.
4. Tools run on their own: standard library and third-party packages only, or as thin
   roots over app units.
5. Units log through `logging.getLogger("logos.<unit>")`; only roots configure logging.

## Checking

```bash
python3 scripts/check_independence.py --summary    # counters E R S V I L A W D
python3 scripts/check_independence.py --only E     # list cross-unit references
python3 scripts/check_independence.py --mermaid    # regenerate the map below
python3 scripts/check_independence.py --routes     # static route table
python3 scripts/check_independence.py --entrypoints
```

The check is static and manual; it is not a CI gate. The refactor is finished when it
prints `TOTAL=0`.

## Current map

Arrows are cross-unit references (count on the edge) between application units.

<!-- BEGIN GENERATED: unit map (scripts/check_independence.py --mermaid) -->
```mermaid
flowchart LR
    roots([composition roots])
    agent_context[agent_context]
    agent_memory[agent_memory]
    agent_office[agent_office]
    agent_retrieval[agent_retrieval]
    agent_rules[agent_rules]
    agent_runtime[agent_runtime]
    agent_shell[agent_shell]
    agent_skills[agent_skills]
    agents[agents]
    api[api]
    browser[browser]
    chat[chat]
    cloud[cloud]
    conversations[conversations]
    core[core]
    economy[economy]
    identity[identity]
    invocation[invocation]
    keel[keel]
    kristall[kristall]
    mcp_server[mcp_server]
    memory_engine[memory_engine]
    memory_log_adapter[memory_log_adapter]
    model_editor[model_editor]
    observability[observability]
    organization[organization]
    plugin_system[plugin_system]
    printer_service[printer_service]
    printing3d[printing3d]
    project_board[project_board]
    provider_accounts[provider_accounts]
    rdc_connection[rdc_connection]
    recovery[recovery]
    remote_input[remote_input]
    remote_runtime[remote_runtime]
    repository_index[repository_index]
    runtime[runtime]
    secret_store[secret_store]
    sessions[sessions]
    steel[steel]
    storage[storage]
    tool_providers[tool_providers]
    workspace[workspace]
    yandex_client_modules[yandex_client_modules]
    agent_context -->|1| observability
    agent_memory -->|1| core
    agent_memory -->|4| kristall
    agent_memory -->|1| observability
    agent_memory -->|3| storage
    agent_office -->|1| core
    agent_retrieval -->|1| agent_context
    agent_retrieval -->|2| agent_memory
    agent_retrieval -->|1| observability
    agent_retrieval -->|1| repository_index
    agent_rules -->|1| tool_providers
    agent_runtime -->|1| chat
    agent_runtime -->|5| core
    agent_runtime -->|3| invocation
    agent_runtime -->|1| provider_accounts
    agent_runtime -->|1| runtime
    agent_runtime -->|1| secret_store
    agent_runtime -->|3| storage
    agent_runtime -->|4| tool_providers
    agent_shell -->|1| agent_memory
    agent_shell -->|1| agent_runtime
    agent_shell -->|1| core
    agent_shell -->|1| kristall
    agent_skills -->|1| core
    chat -->|3| agent_runtime
    chat -->|1| agent_skills
    chat -->|3| conversations
    chat -->|9| core
    chat -->|5| economy
    chat -->|2| invocation
    chat -->|2| mcp_server
    chat -->|5| observability
    chat -->|5| provider_accounts
    chat -->|4| storage
    chat -->|2| tool_providers
    chat -->|12| yandex_client_modules
    cloud -->|10| core
    cloud -->|1| economy
    cloud -->|6| observability
    cloud -->|1| secret_store
    cloud -->|1| storage
    cloud -->|2| tool_providers
    conversations -->|2| agent_memory
    conversations -->|1| storage
    economy -->|1| cloud
    economy -->|7| core
    economy -->|1| kristall
    economy -->|1| observability
    economy -->|3| storage
    invocation -->|1| agent_memory
    invocation -->|1| core
    invocation -->|2| kristall
    invocation -->|3| observability
    invocation -->|1| sessions
    invocation -->|2| storage
    mcp_server -->|1| agent_runtime
    mcp_server -->|1| conversations
    mcp_server -->|2| core
    mcp_server -->|2| economy
    mcp_server -->|3| invocation
    mcp_server -->|1| observability
    mcp_server -->|1| runtime
    mcp_server -->|1| secret_store
    mcp_server -->|1| sessions
    mcp_server -->|2| storage
    mcp_server -->|5| tool_providers
    memory_engine -->|2| core
    memory_engine -->|1| kristall
    model_editor -->|1| economy
    model_editor -->|2| storage
    model_editor -->|1| tool_providers
    observability -->|1| core
    observability -->|3| economy
    organization -->|3| core
    organization -->|2| economy
    organization -->|1| sessions
    organization -->|3| storage
    organization -->|3| tool_providers
    plugin_system -->|1| core
    plugin_system -->|1| invocation
    plugin_system -->|1| runtime
    plugin_system -->|1| tool_providers
    printing3d -->|1| core
    printing3d -->|2| economy
    printing3d -->|1| storage
    printing3d -->|1| tool_providers
    project_board -->|2| core
    project_board -->|1| memory_engine
    provider_accounts -->|3| cloud
    provider_accounts -->|8| core
    provider_accounts -->|1| economy
    provider_accounts -->|2| observability
    provider_accounts -->|3| secret_store
    provider_accounts -->|3| storage
    provider_accounts -->|3| yandex_client_modules
    rdc_connection -->|1| core
    remote_runtime -->|2| agent_memory
    remote_runtime -->|6| core
    remote_runtime -->|1| economy
    remote_runtime -->|2| invocation
    remote_runtime -->|2| observability
    remote_runtime -->|2| runtime
    remote_runtime -->|2| storage
    runtime -->|1| api
    runtime -->|1| conversations
    runtime -->|1| economy
    runtime -->|3| invocation
    runtime -->|1| observability
    runtime -->|2| sessions
    runtime -->|1| storage
    runtime -->|1| tool_providers
    secret_store -->|5| cloud
    secret_store -->|3| core
    secret_store -->|1| storage
    sessions -->|1| agent_memory
    sessions -->|3| core
    sessions -->|1| storage
    storage -->|2| agent_memory
    storage -->|1| cloud
    storage -->|2| core
    storage -->|1| kristall
    storage -->|1| runtime
    tool_providers -->|1| agent_runtime
    tool_providers -->|1| cloud
    tool_providers -->|6| core
    tool_providers -->|1| kristall
    tool_providers -->|1| model_editor
    tool_providers -->|2| organization
    tool_providers -->|1| printing3d
    tool_providers -->|2| remote_runtime
    tool_providers -->|1| storage
    workspace -->|3| core
    workspace -->|4| yandex_client_modules
    yandex_client_modules -->|1| agent_memory
    yandex_client_modules -->|7| core
    yandex_client_modules -->|2| economy
    yandex_client_modules -->|1| mcp_server
    yandex_client_modules -->|5| observability
    yandex_client_modules -->|2| provider_accounts
    yandex_client_modules -->|1| secret_store
    yandex_client_modules -->|2| storage
    yandex_client_modules -->|2| tool_providers
    yandex_client_modules -->|1| workspace
```
<!-- END GENERATED: unit map -->
