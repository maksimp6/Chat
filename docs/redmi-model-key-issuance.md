# Runner-issued model key on Redmi 9

The workflow `Redmi 9 issue model key` has one Bash step named `logos`.
The runner process issues a Yandex Cloud API key through IAM and delivers it
to the existing app at `127.0.0.1:8080`. The app never issues the key itself.
There are no model, tokenization, model-list or paid validation requests.

The runner requires these Actions secrets:

- `YANDEX_IAM_TOKEN`: an existing IAM authorization that may create keys for
  the selected service account.
- `YANDEX_SERVICE_ACCOUNT_ID`: the existing service account.
- `YANDEX_FOLDER_ID`: the folder containing that service account.
- `LOGOS_PROVIDER_CREDENTIALS_TOKEN`: only when the local app requires this
  administrative token.

No GitHub token can replace Yandex IAM authorization. The job does not create
service accounts or assign roles. Its API-key scope is
`yc.ai.foundationModels.execute`; lifetime is 12 hours, matching the app's
existing credential lifetime. This one-shot job does not configure scheduled
rotation. An existing app credential is never silently replaced.

Before issuing, the job checks the app's encryption readiness and the service
account's folder. It saves the issued resource ID, exact expiration and secret
via `POST /api/provider-credentials/yandex/issued`. The app encrypts the secret
before the durable native journal transaction, reads it back, and returns only
metadata. The runner then reads metadata again and verifies the fingerprint.
The status is `configured`; model authorization remains unverified.

The new secret exists only in the runner's and app's process memory. It is not
printed, committed, stored in browser storage, or put in LFS. An uncertain
issuance/delivery leaves a mode-600 marker in
`~/logos-local-state/yandex-issuance.pending.json` containing only state and
the resource ID when known. A rerun stops at this marker instead of issuing a
duplicate. Inspect the provider and app before resolving this state; never
remove the marker and retry blindly. A definite app rejection before writing
revokes only the new key. An ambiguous response never revokes a possibly saved
key.

The app import uses its existing administrator guard, rejects cross-origin
JSON imports and ordinary form posts, and disables request-body tracing.
Tests use synthetic IAM responses and real encrypted journal reloads; they
do not call a model.

Sources:
- https://yandex.cloud/en/docs/iam/operations/authentication/manage-api-keys
- https://yandex.cloud/en/docs/iam/api-ref/ApiKey/create
- https://aistudio.yandex.ru/en/docs/ai-studio/operations/generation/create-prompt
