# Environment variables

Generated from `environment-variables.json`. Values and secret material are intentionally excluded.

Coverage: statically named environment reads in repository Python outside tests. Dynamic names and shell/JavaScript/Kotlin environment access are follow-up surfaces, not silently assumed covered.

| Variable | Class | Purpose |
| --- | --- | --- |
| `ALICE_AGENT_MODEL` | public | agent execution model override |
| `ALICE_BASE_URL` | public | Alice service base URL used by clients/tools |
| `ALICE_CLI_TITLE` | public | CLI display title |
| `ALICE_CLOUD_PROVIDER` | public | selected cloud-provider adapter |
| `ALICE_CONVERSATION_ID` | public | conversation context identifier for CLI/runtime |
| `ALICE_DATABASE_URL` | secret | SQL database connection URL |
| `ALICE_DB_BACKEND` | compatibility | legacy database backend selector |
| `ALICE_DB_PATH` | compatibility | legacy SQLite database path |
| `ALICE_DEPARTMENTS_ADMIN_TOKEN` | secret | backend credential required by mutating department endpoints via X-Department-Admin-Token |
| `ALICE_DONATION_URL` | public | support/donation URL exposed by the application |
| `ALICE_ENV_PUBLIC_BASE_URL` | public | public base URL for branch environments |
| `ALICE_ENV_REPO_ROOT` | public | repository root used by branch environment manager |
| `ALICE_ENV_RUNTIME_ROOT` | public | runtime workspace root for branch environments |
| `ALICE_GITHUB_ALLOWED_IDS` | public | allowlist of GitHub account IDs |
| `ALICE_GITHUB_CLIENT_ID` | public | GitHub OAuth client identifier |
| `ALICE_GITHUB_CLIENT_SECRET` | secret | GitHub OAuth client secret |
| `ALICE_GITHUB_REDIRECT_URI` | public | GitHub OAuth callback URI |
| `ALICE_KEY_MANAGER_KEY` | secret | legacy Key Manager encryption key |
| `ALICE_LAUNCH_SMOKE_YANDEX_API_KEY` | secret | Yandex credential used only by live launch smoke |
| `ALICE_LAUNCH_SMOKE_YANDEX_PROJECT_ID` | ci-only | Yandex project used only by live launch smoke |
| `ALICE_LOCAL_AGENT_BOOTSTRAP_TOKEN` | secret | Local Tool Agent registration bootstrap credential |
| `ALICE_LOCAL_REPO_DIR` | compatibility | legacy/local-files repository directory |
| `ALICE_MCP_ALLOW_ANONYMOUS` | public | compatibility switch allowing anonymous MCP access |
| `ALICE_MCP_BEARER_TOKEN` | secret | MCP bearer credential |
| `ALICE_MCP_INTROSPECTION_CLIENT_ID` | public | OAuth introspection client identifier |
| `ALICE_MCP_INTROSPECTION_CLIENT_SECRET` | secret | OAuth introspection client secret |
| `ALICE_MCP_INTROSPECTION_TIMEOUT` | public | OAuth introspection timeout in seconds |
| `ALICE_MCP_INTROSPECTION_URL` | public | OAuth token introspection endpoint |
| `ALICE_MCP_OAUTH_ISSUER` | public | MCP OAuth issuer URL |
| `ALICE_MCP_OAUTH_SCOPE` | public | MCP OAuth scope |
| `ALICE_MCP_PUBLIC_URL` | public | public MCP resource URL |
| `ALICE_MCP_USER_ID` | public | MCP user identity override |
| `ALICE_MEMORY_PATH` | public | file-native Memory DB path |
| `ALICE_MODEL` | public | default Alice model |
| `ALICE_OWNER_ID` | public | owner identity identifier |
| `ALICE_PLUGIN_DIR` | public | plugin discovery directory |
| `ALICE_PREVIEW_BASE_PATH` | public | preview deployment URL prefix |
| `ALICE_PROJECT_ROOT` | public | Alice repository/project root |
| `ALICE_PROVIDER_CREDENTIALS_TOKEN` | secret | provider-credential administration credential |
| `ALICE_PROVIDER_CREDENTIAL_KEY` | secret | legacy provider credential encryption key |
| `ALICE_QUOTA_ADMIN_TOKEN` | secret | provider quota administration credential |
| `ALICE_QUOTA_REQUIRE_IDENTITY` | public | require trusted identity for provider quota enforcement |
| `ALICE_RDC_PAIRING_FILE` | compatibility | path to the RDC pairing handoff JSON consumed by the redirect/session bridge |
| `ALICE_RDC_PROJECT_ID` | compatibility | Cloud.ru project UUID bound to RDC persistent state and control permits |
| `ALICE_RDC_STATE_PATH` | compatibility | mounted directory containing persistent RDC state and control-permit files |
| `ALICE_REQUIRE_SHORT_TOKEN` | public | enable short-token authentication gate |
| `ALICE_SESSION_ID` | public | runtime session context identifier |
| `ALICE_SHELL_DB` | compatibility | SQLite file selected by the legacy agent_shell CLI when --db is not provided |
| `ALICE_SHORT_TOKEN` | secret | short-token bootstrap/auth credential |
| `ALICE_SHORT_TOKEN_TTL_SECONDS` | public | short-token session lifetime in seconds |
| `ALICE_SSH_KNOWN_HOSTS` | public | SSH known-hosts source/path |
| `ALICE_SSH_TARGETS_JSON` | public | SSH target configuration JSON |
| `ALICE_STATIC_VERSION` | public | static asset cache/version identifier |
| `ALICE_VERSION` | public | Alice application version |
| `ALICE_VOICE_CHAT_MODEL` | public | model used for voice chat stage |
| `CI_JOB_RESULTS` | ci-only | serialized GitHub Actions job results for CI verification |
| `CI_PLATFORM_PLAN` | ci-only | serialized platform selection plan for CI verification |
| `CLOUDRU_API_KEY` | secret | Cloud.ru API credential |
| `CLOUDRU_API_KEY_ID` | secret-reference | Cloud.ru API key identifier/reference |
| `CLOUDRU_BACKUP_PATH` | public | Cloud.ru backup API path override |
| `CLOUDRU_BASE_URL` | public | Cloud.ru API base URL |
| `CLOUDRU_BILLING_CURRENCY` | public | currency used by Cloud.ru billing adapter |
| `CLOUDRU_BILLING_SUMMARY_PATH` | public | Cloud.ru billing summary API path override |
| `CLOUDRU_COMPUTE_ACTION_PATH` | public | Cloud.ru compute action API path override |
| `CLOUDRU_CONTAINER_CPU` | public | Container Apps CPU allocation |
| `CLOUDRU_CONTAINER_NAME` | public | Container Apps application/container name |
| `CLOUDRU_IAM_ENDPOINT` | public | Cloud.ru IAM API endpoint |
| `CLOUDRU_IAM_KEY_ID` | secret-reference | Cloud.ru IAM key identifier/reference |
| `CLOUDRU_IAM_KEY_SECRET` | secret | Cloud.ru IAM key secret |
| `CLOUDRU_IAM_WIZARD_ENABLED` | public | enable Cloud.ru IAM setup wizard |
| `CLOUDRU_IAM_WIZARD_TOKEN` | secret | authorization credential for Cloud.ru IAM wizard |
| `CLOUDRU_KEY_ID` | secret-reference | legacy alias for Cloud.ru IAM key identifier |
| `CLOUDRU_KEY_SECRET` | secret | legacy alias for Cloud.ru IAM key secret |
| `CLOUDRU_KEY_TTL_DAYS` | public | lifetime for rotated Cloud.ru provider keys |
| `CLOUDRU_MAX_INSTANCES` | public | Container Apps maximum instance count |
| `CLOUDRU_MIN_INSTANCES` | public | Container Apps minimum instance count |
| `CLOUDRU_OBSERVABILITY_LOGS_PATH` | public | Cloud.ru logs API path override |
| `CLOUDRU_OBSERVABILITY_METRICS_PATH` | public | Cloud.ru metrics API path override |
| `CLOUDRU_PROJECT_ID` | public | Cloud.ru project identifier |
| `CLOUDRU_REGISTRY_DOMAIN` | public | Cloud.ru container registry domain |
| `CLOUDRU_REGISTRY_NAME` | public | Cloud.ru registry name |
| `CLOUDRU_REPOSITORY_NAME` | public | Cloud.ru container repository name |
| `CLOUDRU_SECRET_MANAGEMENT_KEY_ID` | secret-reference | IAM key identifier used by Secret Management adapter |
| `CLOUDRU_SECRET_MANAGEMENT_KEY_SECRET` | secret | IAM key secret used by Secret Management adapter |
| `CLOUDRU_STORAGE_TENANT_ID` | public | Cloud.ru Object Storage tenant identifier |
| `EDS_API_KEY` | secret | EDS API credential |
| `EDS_PROJECT_ID` | public | EDS project identifier |
| `FLASK_DEBUG` | public | Flask debug-mode switch |
| `GH_TOKEN` | secret | GitHub CLI/API credential |
| `GITHUB_REPOSITORY` | ci-only | GitHub Actions repository identifier |
| `GITHUB_STEP_SUMMARY` | ci-only | GitHub Actions step-summary file path |
| `GITHUB_TOKEN` | secret | GitHub Actions/API credential |
| `HOME` | public | process home directory; Android embedded server uses it to derive app-local runtime paths |
| `HOST` | public | Flask bind host |
| `IDP_ALLOWED_RESOURCES` | public | OAuth IDP allowed resource list |
| `IDP_PUBLIC_URL` | public | OAuth IDP public URL |
| `MERGE_REQUIRED_CHECKS` | ci-only | required check-name override used by merge readiness |
| `MODEL_DISCOVERY_TIMEOUT_SECONDS` | public | model discovery HTTP timeout |
| `MODEL_DISCOVERY_TTL_SECONDS` | public | model discovery cache lifetime |
| `PORT` | public | Flask bind port |
| `PR_NUMBER` | ci-only | pull request number used by CI/deployment tooling |
| `YANDEX_AI_ENDPOINT` | public | Yandex AI API endpoint |
| `YANDEX_API_KEY` | secret | legacy/direct Yandex API credential |
| `YANDEX_API_KEY_SCOPES` | public | requested scopes for Yandex API keys |
| `YANDEX_BASE_URL` | public | Yandex API base URL |
| `YANDEX_FOLDER_ID` | public | Yandex folder identifier |
| `YANDEX_IAM_ENDPOINT` | public | Yandex IAM API endpoint |
| `YANDEX_IAM_TOKEN` | secret | Yandex IAM bearer credential |
| `YANDEX_PROJECT_ID` | compatibility | legacy Yandex project/folder identifier |
| `YANDEX_PROVIDER_KEY_ID` | secret-reference | Yandex provider key identifier/reference |
| `YANDEX_SERVICE_ACCOUNT_ID` | public | Yandex service-account identifier |
| `YC_API_KEY` | secret | legacy Yandex API key alias |

Classes:

- `public` — non-secret runtime configuration;
- `secret` — credential/bootstrap secret; document purpose, never value;
- `secret-reference` — identifier/version/reference to a secret, not plaintext;
- `ci-only` — test/CI control not intended as application configuration;
- `compatibility` — legacy or transitional runtime input.
