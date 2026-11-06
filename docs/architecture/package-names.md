# Package names

Every module of Logos (a folder in the repository root that holds Python code)
has a brand name and a one-line tagline.
The brand is the public name, used in titles and documentation. The path on
disk, which the code imports and the architecture map uses, does not change.

**Naming rule:** Every unit gets one invented, pronounceable six- or seven-letter word with a consonant-vowel sound pattern, unique in its first five letters, while the existing Kristall is kept as the sole exception.

| Brand | Package | Tagline |
| ----- | ------- | ------- |
| **Telira** | `agent_context` | Builds cached, GitHub-based context packets for agent tasks. |
| **Nemura** | `agent_memory` | Stores shared, source-linked memory facts for Logos agents. |
| **Sovaro** | `agent_office` | Watches agent work on GitHub and flags stuck tasks. |
| **Rekavi** | `agent_retrieval` | Finds bounded, token-budgeted hits across context, memory and repositories. |
| **Dorvane** | `agent_rules` | Proposed agent rule registry and blocking controller; not deployed. |
| **Tavuro** | `agent_runtime` | Provider-agnostic agent gateway, remote tool agents and GitHub issue runner. |
| **Zorumi** | `agent_shell` | Minimal command line to add, run and approve agent tasks on a durable Kristall store. |
| **Pelisu** | `agent_skills` | Finds repository Agent Skills and loads their instructions into chats. |
| **Heravo** | `api` | Holds Yandex API contracts and gates the Yandex calls routed through its transport. |
| **Dulena** | `browser` | Web browsing tools for pages, forms, search and downloads. |
| **Kelura** | `chat` | Serves chat turns, voice, images, embeddings and completions. |
| **Ravusa** | `cloud` | Provider-neutral cloud IAM, secrets, storage and sandboxes for Logos tools. |
| **Lamiro** | `composition` | Wires the separate units together at startup and app assembly. |
| **Gebato** | `conversations` | Tracks conversation ownership, titles, models, settings and deletion. |
| **Ketavi** | `core` | Holds the model catalog, pricing, environment settings and shared helpers. |
| **Veridu** | `court` | Opens GitHub cases for errors and censors rule-breaking replies. |
| **Ferino** | `economy` | Tracks each reply's cost, billing, budgets and paper treasury ledger. |
| **Velisa** | `forge` | Exposes 3D-printing quotes and print orders as routes and tools. |
| **Kalumi** | `invocation` | Manages each invocation's lifecycle, execution trace and request context. |
| **Derako** | `keel` | Reconciles deployments with cloud state, plus health checks and recovery. |
| **Kristall** | `kristall` | Typed key/value store with explicit commits on an append-only journal. |
| **Benora** | `mcp_server` | MCP endpoint: external clients read dialogs and call tools. |
| **Miruka** | `memory_engine` | Standalone key/value store run as a private Unix-socket service. |
| **Gilaro** | `model_editor` | Local 3D scene builder with versioned scenes and STL exports. |
| **Eskaro** | `observability` | Records each invocation's steps, API calls, tools, errors and timing. |
| **Fidalo** | `organization` | Department registry, government case workflow and partner CRM as tools. |
| **Tirona** | `plugin_system` | Discovers local plugins from manifests and manages their state. |
| **Suvema** | `printer_service` | Read-only LAN discovery and telemetry for Bambu A1 mini printers. |
| **Cavori** | `printing3d` | 3D-printing business domain: quotes, slicing, preflight, telemetry and workflow. |
| **Yurena** | `project_board` | Private project board for discussions, incidents, tasks and evidence. |
| **Nuvero** | `provider_accounts` | Stores and rotates provider credentials, lists chat models. |
| **Hadomi** | `rag_sync` | Adds new commits and GitHub activity to chat file search. |
| **Sobeki** | `rdc_connection` | Redirects to an active Remote Desktop Commander pairing page. |
| **Pasuno** | `recovery` | CadQuery script making an unverified two-piece TPU case model. |
| **Lisora** | `remote_input` | Staging-only: turns authorized pointer commands into signed, short-lived grants. |
| **Jolara** | `remote_runtime` | Runs branch environments and commands as Linux users over SSH. |
| **Zerano** | `repository_index` | Validates identity and provenance of immutable repository index snapshots. |
| **Ruvane** | `runtime` | Scoped dispatcher for preview runtimes' database, file and tool access. |
| **Vesara** | `secret_store` | Resolves secret references into redacted values with policy and audit. |
| **Tamoro** | `sessions` | Keeps durable AI sessions. |
| **Bokeri** | `steel` | Fail-closed admission check for worker jobs: approval, capabilities and limits. |
| **Ondera** | `storage` | Saves chats and settings in the Kristall journal and generated images as private files. |
| **Fenaro** | `tool_providers` | Tool contracts, a policy-checked execution pipeline and built-in tool sources. |
| **Remuza** | `vector_db` | Embeds texts with Yandex models and searches them by meaning. |
| **Ocheva** | `web_shell` | Serves the Logos web page and health and readiness endpoints. |
| **Nakeri** | `workspace` | Serves user files, vector stores, uploads and a project tree. |
| **Eduran** | `yandex_client_modules` | Yandex AI Studio client: Responses, streaming, files and image generation. |

The application itself is **Logos**; `app.py` is its entry point.
