from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE_WORKFLOW = WORKFLOWS / "release.yml"


def test_release_has_one_canonical_android_publisher() -> None:
    release_workflows = [
        path
        for path in WORKFLOWS.glob("*.yml")
        if ":app:assembleRelease" in path.read_text(encoding="utf-8")
    ]

    assert release_workflows == [RELEASE_WORKFLOW]


def test_release_keystore_uses_alice_secrets_and_runner_temp() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert 'ALICE_RELEASE_KEYSTORE_PATH="$RUNNER_TEMP/alice-release.jks"' in workflow
    assert '"ALICE_RELEASE_KEYSTORE_PATH=$ALICE_RELEASE_KEYSTORE_PATH" >> "$GITHUB_ENV"' in workflow
    assert "${{ github.workspace }}/android/app/release-signing.keystore" not in workflow
    for secret in (
        "ALICE_RELEASE_KEYSTORE_BASE64",
        "ALICE_RELEASE_STORE_PASSWORD",
        "ALICE_RELEASE_KEY_ALIAS",
        "ALICE_RELEASE_KEY_PASSWORD",
    ):
        assert f"secrets.{secret}" in workflow
    assert "if: always()" in workflow
    assert 'rm -f "$ALICE_RELEASE_KEYSTORE_PATH"' in workflow


def test_release_verifies_identity_and_metadata_before_publish() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    verification = workflow.index("- name: Verify release APK")
    publication = workflow.index("- name: Publish GitHub Release")

    assert verification < publication
    assert "--print-certs" in workflow
    assert 'test "$APK_CERT_SHA256" = "$KEYSTORE_CERT_SHA256"' in workflow
    assert "EXPECTED_APPLICATION_ID: com.alicepro.mobile" in workflow
    assert "versionCode='$GITHUB_RUN_NUMBER'" in workflow
    assert "versionName='${GITHUB_REF_NAME#v}'" in workflow


def test_job_level_env_does_not_use_step_only_contexts() -> None:
    # GitHub rejects the whole workflow when jobs.<id>.env references the runner context.
    for path in WORKFLOWS.glob("*.yml"):
        in_job_env = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            indent = len(line) - len(line.lstrip(" "))
            if line.strip() and indent <= 4:
                in_job_env = line == "    env:"
                continue
            if in_job_env and indent == 6:
                assert "runner." not in line, f"{path.name}:{number} uses runner context in job env"
