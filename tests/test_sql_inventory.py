"""#776: regressions for complete tracked-file discovery and non-growth guard."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.sql_inventory import (
    _tracked_ref,
    _tracked_worktree,
    build_inventory,
    category,
    main,
    new_debt,
    render_markdown,
    wave,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class SQLInventoryTests(unittest.TestCase):
    def test_path_areas_are_explicit(self) -> None:
        self.assertEqual(category("tests/test_postgres_integration.py"), "test")
        self.assertEqual(category(".github/workflows/ci.yml"), "ci")
        self.assertEqual(category("docs/database.md"), "docs")
        self.assertEqual(category("migrations/0001.sql"), "migration")
        self.assertEqual(category("deploy/cloudru/postgres/bootstrap.sql"), "migration")
        self.assertEqual(category("session_manager.py"), "runtime")
        self.assertEqual(category("scripts/pg_backup.sh"), "infra")

    def test_owner_waves(self) -> None:
        self.assertEqual(wave("session_manager.py", "runtime"), "wave-1")
        self.assertEqual(wave("invocation/manager.py", "runtime"), "wave-2")
        self.assertEqual(wave("treasury.py", "runtime"), "wave-3")
        self.assertEqual(wave("db.py", "runtime"), "wave-4")
        self.assertEqual(wave("agent_shell/store.py", "runtime"), "triage")

    def test_scan_tracks_all_text_and_no_content(self) -> None:
        inv = build_inventory(
            [
                ("db.py", b"import sqlite3\nconn = get_conn()\n"),
                ("docs/database.md", b"PostgreSQL, SQLite, pg_dump and ALICE_DATABASE_URL\n"),
                ("tests/test_demo.py", b"SELECT * FROM accounts\n"),
                ("image.png", b"\x89PNG\x00"),
                ("safe.py", b"def ok(): return 1\n"),
            ],
            "fixture-master",
        )
        self.assertEqual(inv["tracked_files"], 5)
        self.assertEqual(inv["scanned_text_files"], 4)
        self.assertEqual(inv["direct_runtime_files"], 1)
        self.assertEqual(inv["matched_by_category"]["docs"], 1)
        self.assertIn("db.py", render_markdown(inv))
        self.assertNotIn("accounts", render_markdown(inv))
        self.assertEqual(inv["skipped_files"][0]["path"], "image.png")

    def test_comments_do_not_add_runtime_direct_debt(self) -> None:
        inv = build_inventory([("demo.py", b"# get_conn() legacy\nprint('hello')\n")], "src")
        self.assertEqual(inv["direct_runtime_files"], 0)

    def test_new_sql_conn_fails_guard(self) -> None:
        before = build_inventory([("session_manager.py", b"def f(): return 1\n")], "base")
        after = build_inventory([("session_manager.py", b"from db import get_conn\nconn = get_conn()\n")], "head")
        self.assertEqual(new_debt(before, after), [{"path": "session_manager.py", "rule": "sql_connection", "baseline": 0, "current": 1}])

    def test_no_growth_allows_migration_and_docs(self) -> None:
        base = build_inventory([("session_manager.py", b"get_conn()\nget_conn()\n")], "master")
        head = build_inventory([
            ("session_manager.py", b"get_conn()\n"),
            ("docs/database.md", b"get_conn() psycopg sqlite3 SELECT * FROM users\n"),
            ("tests/test_sql.py", b"import sqlite3\n"),
        ], "head")
        self.assertEqual(new_debt(base, head), [])

    def test_unknown_sql_owner_is_not_silently_assigned(self) -> None:
        inv = build_inventory([("agent_shell/store.py", b"import sqlite3\n")], "master")
        self.assertEqual(inv["unknown_runtime_direct"], ["agent_shell/store.py"])

    def test_git_ref_inventory_is_read_only_and_guard_works(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _git(root, "init", "-q")
            _git(root, "config", "user.email", "inventory@example.invalid")
            _git(root, "config", "user.name", "Inventory CI")
            (root / "db.py").write_text("import sqlite3\n", encoding="utf-8")
            (root / "notes.txt").write_text("reference\n", encoding="utf-8")
            _git(root, "add", "-A")
            _git(root, "commit", "-qm", "baseline")
            base = build_inventory(_tracked_ref(root, "HEAD"), "HEAD")
            self.assertEqual(base["tracked_files"], 2)
            self.assertEqual(new_debt(base, build_inventory(_tracked_worktree(root), "worktree")), [])
            (root / "session_manager.py").write_text("get_conn()\n", encoding="utf-8")
            _git(root, "add", "session_manager.py")
            head = build_inventory(_tracked_worktree(root), "worktree")
            self.assertEqual(len(new_debt(base, head)), 1)
            self.assertEqual(main(["--root", str(root), "--base-ref", "HEAD", "--guard"]), 1)
            (root / "session_manager.py").write_text("file_native()\n", encoding="utf-8")
            self.assertEqual(main(["--root", str(root), "--base-ref", "HEAD", "--guard"]), 0)

    def test_missing_baseline_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _git(root, "init", "-q")
            (root / "safe.py").write_text("pass\n", encoding="utf-8")
            _git(root, "add", "safe.py")
            self.assertEqual(main(["--root", str(root), "--ref", "MISSING"]), 2)


if __name__ == "__main__":
    unittest.main()