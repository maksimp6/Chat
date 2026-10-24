# Android release signing

Logos keeps debug and release signing separate. The former release packager has
been removed under #1423; the tag workflow currently refuses publication before
accessing signing credentials. The direct debug browser controller is not a
replacement for the full embedded-backend application.

## GitHub Actions secrets

These existing secret names are reserved for a future verified release packager;
this removal does not read, create, replace or delete their values:

- `ALICE_RELEASE_KEYSTORE_BASE64`: base64-encoded release keystore.
- `ALICE_RELEASE_KEY_ALIAS`: key alias.
- `ALICE_RELEASE_STORE_PASSWORD`: keystore password.
- `ALICE_RELEASE_KEY_PASSWORD`: private-key password.

A future release packager must materialize the keystore only under the runner temporary directory and remove it unconditionally. It must never be committed to Git or placed in an issue, pull request, log, trace, or chat message.

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

Currently it fails with an explicit packaging-unavailable message. It neither
loads a keystore nor creates a GitHub Release.

Before publication is re-enabled, a replacement must:

1. package the required application features, without substituting the direct controller;
2. verify the APK with apksigner and match its certificate to the approved release key;
3. verify application ID, versionCode/versionName and source/digest metadata;
4. pass device-level verification and owner acceptance;
5. publish only that verified artifact and clean temporary signing material on failure.

## Recovery

Keep at least two offline copies of the original keystore and the passwords in separate secure storage. The keystore is the identity of the application release line: losing it is not the sort of adventure anyone needs.

If the signing key is compromised, revoke the operational secret immediately and follow the release/distribution platform's key-rotation procedure where applicable. Do not replace the GitHub secret casually without planning the compatibility implications for installed updates.
