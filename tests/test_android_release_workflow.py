from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def test_only_canonical_android_release_workflow_exists():
    assert WORKFLOW.is_file()
    assert not (WORKFLOW.parent / "android-release.yml").exists()


def test_release_keystore_is_temporary_and_always_removed():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "ALICE_RELEASE_KEYSTORE_PATH: ${{ runner.temp }}/alice-release.jks" in workflow
    assert "if: always()\n        run: rm -f \"$ALICE_RELEASE_KEYSTORE_PATH\"" in workflow
    assert "${{ github.workspace }}/android/app/release-signing.keystore" not in workflow


def test_release_verifies_update_compatibility_before_publish():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    verify = workflow.index("- name: Verify release APK")
    publish = workflow.index("- name: Publish GitHub Release")

    assert verify < publish
    verification_step = workflow[verify:publish]
    assert "EXPECTED_PACKAGE: com.alicepro.mobile" in verification_step
    assert "versionCode='$EXPECTED_VERSION_CODE'" in verification_step
    assert "versionName='$VERSION_NAME'" in verification_step
    assert 'test "$APK_CERT_SHA256" = "$KEYSTORE_CERT_SHA256"' in verification_step
