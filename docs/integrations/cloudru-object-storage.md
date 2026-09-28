# Cloud.ru Object Storage adapter (issue #443)

`cloud.cloudru.object_storage.CloudRuObjectStorage` implements the `StorageProvider`
contract from `storage.py` (`upload`, `download`, `list`) on top of Cloud.ru
Evolution Object Storage. Object Storage speaks the S3 API; requests are signed
with AWS Signature V4 over `requests`, so no SDK dependency is added.

## Selecting the provider

`storage.storage_provider_from_env()` builds the configured provider:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALICE_STORAGE_PROVIDER` | `local` | `local` or `cloudru` |
| `ALICE_STORAGE_LOCAL_ROOT` | `data/storage` | Root of the local adapter |
| `CLOUDRU_STORAGE_BUCKET` | (required for `cloudru`) | Bucket name |
| `CLOUDRU_STORAGE_S3_ENDPOINT` | `https://s3.cloud.ru` | S3 endpoint (separate from the inventory `CLOUDRU_STORAGE_ENDPOINT`) |
| `CLOUDRU_STORAGE_REGION` | `ru-central-1` | SigV4 region |
| `CLOUDRU_STORAGE_PREFIX` | empty | Key prefix inside the bucket, e.g. `prod` |
| `CLOUDRU_STORAGE_TENANT_ID` | empty | Tenant ID prepended to the access key ID |
| `CLOUDRU_STORAGE_KEY_ID` / `CLOUDRU_STORAGE_KEY_SECRET` | empty | Optional dedicated Object Storage access key |

Buckets are not created by the application; create one in the Cloud.ru console
first.

## Credentials

Register `cloudru_storage_credential_resolver(...)` with
`RuntimeDispatcher.set_storage_credential_resolver`. It resolves the Cloud.ru
service account access key, in order:

1. a dedicated key from `CLOUDRU_STORAGE_KEY_ID` / `CLOUDRU_STORAGE_KEY_SECRET`;
2. the stored IAM key from `provider_credentials.get_cloudru_iam_credentials`,
   when a loader is passed;
3. `CLOUDRU_IAM_KEY_ID` / `CLOUDRU_IAM_KEY_SECRET`.

Only service account access keys work for S3 signing. The rotated Foundation
Models API key in `provider_credentials` is a different kind of key and is never
used for storage.

Cloud.ru expects the S3 access key ID as `<tenant_id>:<key_id>`; set
`CLOUDRU_STORAGE_TENANT_ID` so the resolver adds the prefix. The service account
needs an Object Storage role on the bucket.

The secret stays inside the dispatcher and the adapter: `S3Credentials.__repr__`
hides it and API failures surface only as `StorageError`,
`StorageObjectNotFound`, `StorageCredentialsMissing` (missing or rejected keys)
or `StorageProviderUnavailable` (network errors and 5xx), without response
bodies.
