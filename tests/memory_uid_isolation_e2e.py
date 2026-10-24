"""Linux two-UID filesystem isolation E2E; run only as root on isolated CI runner."""

import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile


def run_as(user: str, code: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["runuser", "-u", user, "--", sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("SKIPPED: requires isolated root runner and two host UIDs")
    owner = pwd.getpwnam("memorydb_owner")
    attacker = pwd.getpwnam("memorydb_attacker")
    assert owner.pw_uid != attacker.pw_uid
    with tempfile.TemporaryDirectory(prefix="memory-uid-e2e-") as tmp:
        root = Path(tmp)
        root.chmod(0o755)
        shutil.copytree(
            Path(__file__).resolve().parents[1] / "memory_engine", root / "memory_engine"
        )
        for item in (root / "memory_engine").rglob("*"):
            item.chmod(0o755 if item.is_dir() else 0o644)
        (root / "memory_engine").chmod(0o755)
        private = root / "private"
        private.mkdir(mode=0o700)
        os.chown(private, owner.pw_uid, owner.pw_gid)
        private.chmod(0o700)
        journal = private / "journal.memory"
        owner_code = (
            "import os,sys; sys.path.insert(0,sys.argv[2]); from memory_engine import MemoryStore; "
            "os.environ['ALICE_MEMORY_TRUSTED_ROOT']='1'; "
            "s=MemoryStore(sys.argv[1]); s.set('safe',1); s.commit(); s.close()"
        )
        result = run_as("memorydb_owner", owner_code, str(journal), str(root))
        assert result.returncode == 0, result.stderr
        assert journal.stat().st_uid == owner.pw_uid
        assert journal.stat().st_mode & 0o777 == 0o600
        attack_code = (
            "import os,sys; from pathlib import Path; "
            "p=Path(sys.argv[1]); "
            "p.rename(p.with_name('replaced.memory'))"
        )
        result = run_as("memorydb_attacker", attack_code, str(journal))
        assert result.returncode != 0 and "PermissionError" in result.stderr, result.stderr
        assert journal.exists()
        result = run_as(
            "memorydb_attacker",
            "import os,sys; from pathlib import Path; Path(sys.argv[1]).unlink()",
            str(journal),
        )
        assert result.returncode != 0 and "PermissionError" in result.stderr, result.stderr
        backup = private / "backup.memory"
        restored = private / "restored.memory"
        backup_code = (
            "import os,sys; sys.path.insert(0,sys.argv[3]); "
            "from memory_engine import MemoryStore; "
            "os.environ['ALICE_MEMORY_TRUSTED_ROOT']='1'; "
            "s=MemoryStore(sys.argv[1]); s.backup(sys.argv[2]); s.close()"
        )
        result = run_as("memorydb_owner", backup_code, str(journal), str(backup), str(root))
        assert result.returncode == 0, result.stderr
        assert backup.exists()
        for protected in (journal, backup):
            result = run_as("memorydb_attacker", attack_code, str(protected))
            assert result.returncode != 0 and "PermissionError" in result.stderr
            assert protected.exists()
        restore_code = (
            "import os,sys; sys.path.insert(0,sys.argv[3]); "
            "from memory_engine import MemoryStore; "
            "os.environ['ALICE_MEMORY_TRUSTED_ROOT']='1'; "
            "s=MemoryStore(sys.argv[2]); s.restore(sys.argv[1]); "
            "assert s.get('safe') == 1; s.close()"
        )
        result = run_as("memorydb_owner", restore_code, str(backup), str(restored), str(root))
        assert result.returncode == 0, result.stderr
        result = run_as("memorydb_attacker", attack_code, str(restored))
        assert result.returncode != 0 and "PermissionError" in result.stderr
        assert restored.exists()
        print("PASS: distinct host UIDs, trusted root, journal/backup/restore rename denied")


if __name__ == "__main__":
    main()
