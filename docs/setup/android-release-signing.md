# Android release signing

Alice Pro keeps debug and release signing separate.

## GitHub Actions secrets

Configure these repository secrets before running `Android release APK`:

- `ALICE_RELEASE_KEYSTORE_BASE64`: base64-encoded release keystore.
- `ALICE_RELEASE_KEY_ALIAS`: key alias.
- `ALICE_RELEASE_STORE_PASSWORD`: keystore password.
- `ALICE_RELEASE_KEY_PASSWORD`: private-key password.

The keystore is materialized under the GitHub runner's temporary directory only for the release job and removed in an unconditional cleanup step. It is never committed to Git and must not be placed in an issue, pull request, log, trace, or chat message.

## Generate the keystore once

Run this locally on a trusted machine and keep the original keystore in a separate secure backup:

```bash
keytool -genkeypair \
  -keystore alice-pro-release.keystore \
  -alias alice-pro-release \
  -keyalg RSA \
  -keysize 4096 \
  -validity 10000 \
  -storetype PKCS12
```

Verify the certificate fingerprint:

```bash
keytool -list -v \
  -keystore alice-pro-release.keystore \
  -alias alice-pro-release
```

Create the GitHub Actions secret value without committing the file:

```bash
base64 -w 0 alice-pro-release.keystore
```

On macOS, use `base64 < alice-pro-release.keystore | tr -d '\\n'` instead.

## Release flow

The canonical `Release` workflow runs for semantic version tags matching `vMAJOR.MINOR.PATCH`.

It:

1. materializes the keystore from the protected GitHub secret;
2. builds `assembleRelease` with the release signing configuration;
3. verifies the resulting APK with `apksigner`;
4. checks that the APK signer is the certificate from the configured release keystore;
5. checks the package name, tag-derived `versionName`, and run-derived `versionCode`;
6. publishes the APK, SHA-256 checksum, and release metadata;
7. deletes the temporary keystore even when an earlier step fails.

Debug CI continues to use the standard Android debug signing configuration.

## Recovery

Keep at least two offline copies of the original keystore and the passwords in separate secure storage. The keystore is the identity of the application release line: losing it is not the sort of adventure anyone needs.

If the signing key is compromised, revoke the operational secret immediately and follow the release/distribution platform's key-rotation procedure where applicable. Do not replace the GitHub secret casually without planning the compatibility implications for installed updates.
