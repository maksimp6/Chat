# Stable debug APK signing

CI debug APKs normally use a temporary Gradle debug key. Such APKs cannot reliably update an installation signed by another CI run.

Alice Pro can use a repository-managed debug keystore so that successive debug APKs are signed by the same key and can update one another, provided the application ID remains unchanged.

## Required GitHub Actions secrets

Configure these repository secrets once:

- `ANDROID_DEBUG_KEYSTORE_BASE64`: base64-encoded keystore file.
- `ANDROID_DEBUG_KEY_ALIAS`: alias of the debug key.
- `ANDROID_DEBUG_STORE_PASSWORD`: keystore password.
- `ANDROID_DEBUG_KEY_PASSWORD`: private-key password.

The keystore must be generated and backed up by the project owner. It must never be committed to Git, pasted into issues, or printed in CI logs.

## Generate the key once

On a trusted machine:

```bash
keytool -genkeypair \
  -keystore alice-pro-debug.keystore \
  -alias alice-pro-debug \
  -keyalg RSA \
  -keysize 4096 \
  -validity 10000 \
  -storetype PKCS12
```

Encode it for the GitHub secret:

```bash
base64 -w 0 alice-pro-debug.keystore
```

On macOS:

```bash
base64 < alice-pro-debug.keystore | tr -d '\n'
```

Keep multiple offline backups of the original keystore and store the passwords separately. Losing this key means future debug builds cannot update existing installations signed with it.

If the secrets are absent, CI deliberately falls back to the standard ephemeral Gradle debug key so that ordinary pull requests continue to build. This fallback is not suitable for update testing.
