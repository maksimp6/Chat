# Release process

Alice Pro uses semantic version tags in the form `vMAJOR.MINOR.PATCH`.

## Release checklist

1. Merge and verify the intended changes on `master`.
2. Update the changelog/release notes as appropriate.
3. Create and push a version tag:

```bash
git tag v0.1.0
git push origin v0.1.0
```

4. GitHub Actions validates the tag, builds the signed Android release APK, verifies the signature, creates a SHA-256 checksum and publishes the GitHub Release.

## Required repository secrets

- `ALICE_RELEASE_KEYSTORE_BASE64`
- `ALICE_RELEASE_STORE_PASSWORD`
- `ALICE_RELEASE_KEY_ALIAS`
- `ALICE_RELEASE_KEY_PASSWORD`

The keystore is decoded only below the runner's temporary directory, is removed by an
always-run cleanup step, and is never uploaded as an artifact.

## Build metadata

The Android release receives:

- `versionName` from the Git tag without the leading `v`;
- `versionCode` from the GitHub Actions run number;
- the source commit SHA as build metadata.

## Broken releases

Do not delete or rewrite release tags casually. Mark a broken release clearly, publish a corrective patch release, and document the issue in the release notes. Database rollback remains a separate operational process.

## Verification

Every release should have:

- a signed APK attached to the GitHub Release;
- a SHA-256 checksum;
- release metadata containing tag, commit and build number.

Before publication, CI also checks that the APK package is `com.alicepro.mobile`, its
version name and code match the release tag and workflow run number, and its signing
certificate matches the configured release keystore. These checks prevent publishing
an artifact which cannot update the expected application even if the Gradle build
itself succeeds.

The workflow intentionally fails when signing secrets are absent instead of silently publishing a debug or unsigned APK as a production release.
