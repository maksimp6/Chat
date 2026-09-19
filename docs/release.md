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

The keystore is decoded only in the runner workspace and is not uploaded as a standalone artifact.

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

The workflow intentionally fails when signing secrets are absent instead of silently publishing a debug or unsigned APK as a production release.