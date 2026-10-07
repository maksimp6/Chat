# Environment variables

Generated from `environment-variables.json`. Values and secret material are intentionally excluded.

| Variable | Class | Purpose |
| --- | --- | --- |
| `ALICE_AGENT_MODEL` | public | alice agent model |
| `ALICE_BASE_URL` | public | alice base url |
| `ALICE_CLI_TITLE` | public | alice cli title |
| `ALICE_CLOUD_PROVIDER` | public | alice cloud provider |
| `ALICE_CONVERSATION_ID` | public | alice conversation id |
| `ALICE_DATABASE_URL` | secret | SQL database connection URL |
| `ALICE_DB_BACKEND` | compatibility | alice db backend |
| `ALICE_DB_PATH` | compatibility | alice db path |
| `ALICE_DEPARTMENTS_ADMIN_TOKEN` | secret | alice departments admin token |
| `ALICE_DONATION_URL` | public | alice donation url |
| `ALICE_ENV_PUBLIC_BASE_URL` | public | alice env public base url |
| `ALICE_ENV_REPO_ROOT` | public | alice env repo root |
| `ALICE_ENV_RUNTIME_ROOT` | public | alice env runtime root |
| `ALICE_GITHUB_ALLOWED_IDS` | public | alice github allowed ids |
| `ALICE_GITHUB_CLIENT_ID` | public | alice github client id |
| `ALICE_GITHUB_CLIENT_SECRET` | secret | GitHub OAuth client secret |
| `ALICE_GITHUB_REDIRECT_URI` | public | alice github redirect uri |
| `ALICE_KEY_MANAGER_KEY` | secret | legacy Key Manager encryption key |
| `ALICE_LAUNCH_SMOKE_YANDEX_API_KEY` | secret | alice launch smoke yandex api key |
| `ALICE_LAUNCH_SMOKE_YANDEX_PROJECT_ID` | ci-only | alice launch smoke yandex project id |
| `ALICE_LOCAL_AGENT_BOOTSTRAP_TOKEN` | secret | Local Tool Agent registration bootstrap credential |
| `ALICE_LOCAL_REPO_DIR` | compatibility | legacy/local-files repository directory |
| `ALICE_MCP_ALLOW_ANONYMOUS` | public | alice mcp allow anonymous |
| `ALICE_MCP_BEARER_TOKEN` | secret | MCP bearer credential |
| `ALICE_MCP_INTROSPECTION_CLIENT_ID` | public | alice mcp introspection client id |
| `ALICE_MCP_INTROSPECTION_CLIENT_SECRET` | secret | alice mcp introspection client secret |
| `ALICE_MCP_INTROSPECTION_TIMEOUT` | public | alice mcp introspection timeout |
| `ALICE_MCP_INTROSPECTION_URL` | public | alice mcp introspection url |
| `ALICE_MCP_OAUTH_ISSUER` | public | alice mcp oauth issuer |
| `ALICE_MCP_OAUTH_SCOPE` | public | alice mcp oauth scope |
| `ALICE_MCP_PUBLIC_URL` | public | alice mcp public url |
| `ALICE_MCP_USER_ID` | public | alice mcp user id |
| `ALICE_MEMORY_PATH` | public | file-native Memory DB path |
| `ALICE_MODEL` | public | alice model |
| `ALICE_OWNER_ID` | public | alice owner id |
| `ALICE_PLUGIN_DIR` | public | alice plugin dir |
| `ALICE_PREVIEW_BASE_PATH` | public | alice preview base path |
| `ALICE_PROJECT_ROOT` | public | alice project root |
| `ALICE_PROVIDER_CREDENTIALS_TOKEN` | secret | provider-credential administration credential |
| `ALICE_PROVIDER_CREDENTIAL_KEY` | secret | legacy provider credential encryption key |
| `ALICE_QUOTA_ADMIN_TOKEN` | secret | alice quota admin token |
| `ALICE_QUOTA_REQUIRE_IDENTITY` | public | alice quota require identity |
| `ALICE_RDC_PAIRING_FILE` | compatibility | alice rdc pairing file |
| `ALICE_RDC_PROJECT_ID` | compatibility | alice rdc project id |
| `ALICE_RDC_STATE_PATH` | compatibility | alice rdc state path |
| `ALICE_REQUIRE_SHORT_TOKEN` | public | alice require short token |
| `ALICE_SESSION_ID` | public | alice session id |
| `ALICE_SHELL_DB` | compatibility | alice shell db |
| `ALICE_SHORT_TOKEN` | secret | short-token bootstrap/auth credential |
| `ALICE_SHORT_TOKEN_TTL_SECONDS` | public | alice short token ttl seconds |
| `ALICE_SSH_KNOWN_HOSTS` | public | alice ssh known hosts |
| `ALICE_SSH_TARGETS_JSON` | public | alice ssh targets json |
| `ALICE_STATIC_VERSION` | public | alice static version |
| `ALICE_VERSION` | public | alice version |
| `ALICE_VOICE_CHAT_MODEL` | public | alice voice chat model |
| `CI_JOB_RESULTS` | ci-only | ci job results |
| `CI_PLATFORM_PLAN` | ci-only | ci platform plan |
| `CLOUDRU_API_KEY` | secret | cloudru api key |
| `CLOUDRU_API_KEY_ID` | secret-reference | cloudru api key id |
| `CLOUDRU_BACKUP_PATH` | public | cloudru backup path |
| `CLOUDRU_BASE_URL` | public | cloudru base url |
| `CLOUDRU_BILLING_CURRENCY` | public | cloudru billing currency |
| `CLOUDRU_BILLING_SUMMARY_PATH` | public | cloudru billing summary path |
| `CLOUDRU_COMPUTE_ACTION_PATH` | public | cloudru compute action path |
| `CLOUDRU_CONTAINER_CPU` | public | cloudru container cpu |
| `CLOUDRU_CONTAINER_NAME` | public | cloudru container name |
| `CLOUDRU_IAM_ENDPOINT` | public | cloudru iam endpoint |
| `CLOUDRU_IAM_KEY_ID` | secret-reference | cloudru iam key id |
| `CLOUDRU_IAM_KEY_SECRET` | secret | Cloud.ru IAM key secret |
| `CLOUDRU_IAM_WIZARD_ENABLED` | public | cloudru iam wizard enabled |
| `CLOUDRU_IAM_WIZARD_TOKEN` | secret | cloudru iam wizard token |
| `CLOUDRU_KEY_ID` | secret-reference | cloudru key id |
| `CLOUDRU_KEY_SECRET` | secret | cloudru key secret |
| `CLOUDRU_KEY_TTL_DAYS` | public | cloudru key ttl days |
| `CLOUDRU_MAX_INSTANCES` | public | cloudru max instances |
| `CLOUDRU_MIN_INSTANCES` | public | cloudru min instances |
| `CLOUDRU_OBSERVABILITY_LOGS_PATH` | public | cloudru observability logs path |
| `CLOUDRU_OBSERVABILITY_METRICS_PATH` | public | cloudru observability metrics path |
| `CLOUDRU_PROJECT_ID` | public | Cloud.ru project identifier |
| `CLOUDRU_REGISTRY_DOMAIN` | public | cloudru registry domain |
| `CLOUDRU_REGISTRY_NAME` | public | cloudru registry name |
| `CLOUDRU_REPOSITORY_NAME` | public | cloudru repository name |
| `CLOUDRU_SECRET_MANAGEMENT_KEY_ID` | secret-reference | cloudru secret management key id |
| `CLOUDRU_SECRET_MANAGEMENT_KEY_SECRET` | secret | cloudru secret management key secret |
| `CLOUDRU_STORAGE_TENANT_ID` | public | cloudru storage tenant id |
| `EDS_API_KEY` | secret | eds api key |
| `EDS_PROJECT_ID` | public | eds project id |
| `FLASK_DEBUG` | public | flask debug |
| `GH_TOKEN` | secret | gh token |
| `GITHUB_REPOSITORY` | ci-only | github repository |
| `GITHUB_STEP_SUMMARY` | ci-only | github step summary |
| `GITHUB_TOKEN` | secret | GitHub Actions/API credential |
| `HOST` | public | host |
| `IDP_ALLOWED_RESOURCES` | public | idp allowed resources |
| `IDP_PUBLIC_URL` | public | idp public url |
| `MERGE_REQUIRED_CHECKS` | ci-only | merge required checks |
| `MODEL_DISCOVERY_TIMEOUT_SECONDS` | public | model discovery timeout seconds |
| `MODEL_DISCOVERY_TTL_SECONDS` | public | model discovery ttl seconds |
| `PORT` | public | port |
| `PR_NUMBER` | ci-only | pr number |
| `YANDEX_AI_ENDPOINT` | public | yandex ai endpoint |
| `YANDEX_API_KEY` | secret | legacy/direct Yandex API credential |
| `YANDEX_API_KEY_SCOPES` | public | yandex api key scopes |
| `YANDEX_BASE_URL` | public | yandex base url |
| `YANDEX_FOLDER_ID` | public | yandex folder id |
| `YANDEX_IAM_ENDPOINT` | public | yandex iam endpoint |
| `YANDEX_IAM_TOKEN` | secret | Yandex IAM bearer credential |
| `YANDEX_PROJECT_ID` | compatibility | Yandex project/folder identifier used by legacy consumers |
| `YANDEX_PROVIDER_KEY_ID` | secret-reference | yandex provider key id |
| `YANDEX_SERVICE_ACCOUNT_ID` | public | yandex service account id |
| `YC_API_KEY` | secret | yc api key |

Classes:

- `public` — non-secret runtime configuration;
- `secret` — credential/bootstrap secret; document purpose, never value;
- `secret-reference` — identifier/version/reference to a secret, not plaintext;
- `ci-only` — test/CI control not intended as application configuration;
- `compatibility` — legacy or transitional runtime input.
