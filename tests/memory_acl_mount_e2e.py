"""Linux ACL and mount-boundary adversarial E2E (isolated privileged runner)."""

import os
from pathlib import Path
import pwd
import shutil
import shlex
import subprocess
import sys
import tempfile


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=check)


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("requires privileged isolated runner")
    owner = pwd.getpwnam("memorydb_owner")
    attacker = pwd.getpwnam("memorydb_attacker")
    assert owner.pw_uid != attacker.pw_uid
    with tempfile.TemporaryDirectory(prefix="memory-boundary-") as tmp:
        root = Path(tmp)
        root.chmod(0o755)
        shutil.copytree(
            Path(__file__).resolve().parents[1] / "memory_engine", root / "memory_engine"
        )
        for entry in (root / "memory_engine").rglob("*"):
            entry.chmod(0o755 if entry.is_dir() else 0o644)
        (root / "memory_engine").chmod(0o755)
        owner_probe = (
            "import os,sys; sys.path.insert(0,sys.argv[2]); "
            "os.environ['LOGOS_MEMORY_TRUSTED_ROOT']='1'; "
            "from memory_engine import MemoryStore; MemoryStore(sys.argv[1])"
        )
        private = root / "private"
        private.mkdir(mode=0o700)
        os.chown(private, owner.pw_uid, owner.pw_gid)
        private.chmod(0o700)
        journal = private / "journal.memory"
        journal.write_bytes(b"protected")
        os.chown(journal, owner.pw_uid, owner.pw_gid)
        journal.chmod(0o600)
        # ACL can grant access to another UID; trusted-root must reject
        # a resulting mode mask that permits group access.
        command("setfacl", "-m", f"u:{attacker.pw_name}:rwx", str(private))
        acl = command("getfacl", "-cp", str(private)).stdout
        assert f"user:{attacker.pw_name}:rwx" in acl
        assert private.stat().st_mode & 0o070 != 0
        # The untrusted UID can now access the directory: demonstrates
        # why DAC/ACL policy must be checked, not only UID identity.
        attack = command(
            "runuser",
            "-u",
            attacker.pw_name,
            "--",
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).rename(sys.argv[2])",
            str(journal),
            str(private / "moved.memory"),
            check=False,
        )
        assert attack.returncode == 0, attack.stderr
        print("ACL_ATTACK_REPRODUCED")
        denied = command(
            "runuser",
            "-u",
            owner.pw_name,
            "--",
            sys.executable,
            "-c",
            owner_probe,
            str(private / "new.memory"),
            str(root),
            check=False,
        )
        assert denied.returncode != 0 and "unsafe journal or lock path" in denied.stderr, (
            denied.stderr
        )
        print("ACL_GRANT_REJECTED_BY_MEMORY_DB")
        command("setfacl", "-b", str(private))
        private.chmod(0o700)
        # Mount boundary: an untrusted bind mount is not a symlink. A
        # separate mount namespace keeps the host runner untouched.
        mounted = root / "mounted"
        mounted.mkdir()
        shell = (
            "mount --bind " + str(private) + " " + str(mounted) + " && "
            "runuser -u "
            + owner.pw_name
            + " -- "
            + sys.executable
            + " -c "
            + shlex.quote(owner_probe)
            + " "
            + str(mounted / "new.memory")
            + " "
            + str(root)
        )
        probe = command(
            "unshare",
            "--mount",
            "--propagation",
            "private",
            "sh",
            "-c",
            shell,
            check=False,
        )
        if "Operation not permitted" in probe.stderr and "mount" in probe.stderr:
            print("MOUNT_E2E_SKIPPED_NO_CAP_SYS_ADMIN")
        else:
            assert probe.returncode != 0
            assert "unsafe journal or lock path" in probe.stderr, probe.stderr
            print("BIND_MOUNT_REJECTED_BY_MEMORY_DB")
        print("PASS: ACL rejection checked; mount capability reported")


if __name__ == "__main__":
    main()
