"""Fixture-only capture tests; never read real Android logs or a real identity."""
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile

import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts"


@pytest.fixture
def android(tmp_path):
    if not shutil.which("age") or not shutil.which("age-keygen"):
        pytest.skip("age is required for the actual encryption round-trip test")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    traces = tmp_path / "traces"
    traces.mkdir()
    def command(name, body):
        path = bindir / name
        path.write_text(body.replace("#!/usr/bin/env bash", "#!" + shutil.which("bash"))
                       .replace("#!/usr/bin/env python3", "#!" + sys.executable))
        path.chmod(0o700)
    # Replace only Android commands/trace destinations inside this test fixture.
    command("su", "#!/usr/bin/env python3\nimport os,sys\n"
            "cmd=sys.argv[-1]\n"
            "if cmd == 'id -u': print('0'); sys.exit(0)\n"
            f"cmd=cmd.replace('/system/bin/', {str(bindir) + '/'!r}).replace('/data/misc/perfetto-traces/', {str(traces) + '/'!r})\n"
            "os.execlp('bash','bash','-c',cmd)\n")
    command("getprop", "#!/usr/bin/env bash\nprintf 'Redmi 9\\n'\n")
    command("dumpsys", "#!/usr/bin/env bash\nprintf 'fixture metrics\\n'\n")
    command("logcat", "#!/usr/bin/env bash\nprintf 'test-precise-location test-private-token\\n'\nexit \"${FAKE_LOGCAT_FAIL:-0}\"\n")
    command("perfetto", "#!/usr/bin/env bash\nwhile (( $# )); do if [[ $1 == -o ]]; then shift; output=$1; fi; shift; done\nprintf 'fixture trace' >\"$output\"\n")
    base = tmp_path / "archives"
    workspace = tmp_path / "job-workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    token_dir = home / ".config/github-actions/maksimp6-Chat"
    token_dir.mkdir(parents=True)
    (token_dir / "token").write_text("fixture-ephemeral-token")
    (token_dir / "token").chmod(0o600)
    (token_dir / "receipt.json").write_text('{"run_id":"1234","attempt":1,"lifetime":"current_job_only"}')
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"],
               HOME=str(home),
               ANDROID_DIAGNOSTICS_HOME=str(base), GITHUB_ACTIONS="true", GITHUB_RUN_ID="1234",
               GITHUB_RUN_ATTEMPT="1", GITHUB_SHA="a" * 40, GITHUB_REPOSITORY="maksimp6/Chat",
               GITHUB_WORKSPACE=str(workspace), GH_TOKEN="fixture-ephemeral-token")
    # Never let an invalid Android shebang fall through PATH to the real su.
    assert subprocess.check_output([str(bindir / "su"), "-c", "printf fixture-only"], env=env) == b"fixture-only"
    (base / "keys").mkdir(parents=True)
    subprocess.run(["age-keygen", "-o", str(base / "keys/archive.agekey")], check=True, capture_output=True)
    return base, env


def run(name, env):
    return subprocess.run(["bash", str(SCRIPTS / name)], env=env, capture_output=True, text=True)


def test_full_logs_are_encrypted_and_identity_reused(android):
    base, env = android
    result = run("redmi_android_diagnostics.sh", env)
    assert result.returncode == 0, result.stderr
    dest = base / "runs/1234-1"
    key = base / "keys/archive.agekey"
    original_key = key.read_bytes()
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    encrypted = (dest / "diagnostics.tar.gz.age").read_bytes()
    assert b"test-private-token" not in encrypted
    assert "test-private-token" not in result.stdout + result.stderr
    plain = subprocess.check_output(["age", "-d", "-i", str(key), str(dest / "diagnostics.tar.gz.age")])
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as archive:
        assert b"test-private-token" in archive.extractfile("./logcat.txt").read()
        assert archive.extractfile("./system.perfetto-trace").read() == b"fixture trace"
    manifest = json.loads((dest / "manifest.json").read_text())
    assert manifest["encrypted"] is True and manifest["perfetto_exit"] == manifest["logcat_exit"] == 0
    assert run("redmi_android_diagnostics.sh", env).returncode == 0
    assert key.read_bytes() == original_key
    assert (dest / "diagnostics.tar.gz.age").read_bytes() == encrypted


def test_capture_failure_cannot_become_success_on_retry(android):
    base, env = android
    env["FAKE_LOGCAT_FAIL"] = "19"
    assert run("redmi_android_diagnostics.sh", env).returncode != 0
    assert (base / "runs/1234-1/diagnostics.tar.gz.age").exists()
    assert run("redmi_android_diagnostics.sh", env).returncode != 0
    assert run("redmi_publish_diagnostics.sh", env).returncode != 0


def test_corrupt_archive_is_rejected_before_any_git_operation(android):
    base, env = android
    assert run("redmi_android_diagnostics.sh", env).returncode == 0
    path = base / "runs/1234-1/diagnostics.tar.gz.age"
    path.write_bytes(path.read_bytes() + b"corrupt")
    assert run("redmi_publish_diagnostics.sh", env).returncode != 0
    assert not list(base.glob("publish.*"))


@pytest.mark.parametrize("script", ["redmi_store_github_token.sh", "redmi_android_diagnostics.sh", "redmi_publish_diagnostics.sh"])
def test_untrusted_run_identifier_rejected_before_capture(tmp_path, script):
    marker = tmp_path / "must-not-exist"
    env = dict(os.environ, GITHUB_ACTIONS="true", GITHUB_REPOSITORY="maksimp6/Chat",
               GITHUB_RUN_ID=f"$(touch {marker})", GITHUB_RUN_ATTEMPT="1")
    assert run(script, env).returncode == 2
    assert not marker.exists()


@pytest.mark.parametrize("reject_auth", [False, True])
def test_github_token_installed_only_after_authenticated_api(tmp_path, reject_auth):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in {
        "su": '#!/usr/bin/env bash\nprintf "Redmi 9\\n"\n',
        "curl": '#!/usr/bin/env bash\ncat >/dev/null\n[[ ${FAKE_AUTH_FAILURE:-0} == 0 ]] || exit 22\nwhile (( $# )); do if [[ $1 == -o ]]; then shift; output=$1; fi; shift; done\nprintf \'{"repositories":[{"full_name":"maksimp6/Chat"}]}\' >"$output"\n',
    }.items():
        path = bindir / name
        path.write_text(body.replace("#!/usr/bin/env bash", "#!" + shutil.which("bash")))
        path.chmod(0o700)
    env = dict(os.environ, HOME=str(tmp_path / "home"), PATH=str(bindir) + os.pathsep + os.environ["PATH"],
               GITHUB_ACTIONS="true", GITHUB_REPOSITORY="maksimp6/Chat", GITHUB_RUN_ID="1234",
               GITHUB_RUN_ATTEMPT="1", GH_TOKEN="fixture-ephemeral-token", FAKE_AUTH_FAILURE=str(int(reject_auth)))
    result = run("redmi_store_github_token.sh", env)
    token = Path(env["HOME"]) / ".config/github-actions/maksimp6-Chat/token"
    assert "fixture-ephemeral-token" not in result.stdout + result.stderr
    if reject_auth:
        assert result.returncode != 0 and not token.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert token.read_text() == "fixture-ephemeral-token"
        assert stat.S_IMODE(token.stat().st_mode) == 0o600
        receipt = json.loads(token.with_name("receipt.json").read_text())
        assert receipt["authenticated"] is True
        assert receipt["lifetime"] == "current_job_only"
