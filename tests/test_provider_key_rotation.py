from datetime import datetime, timedelta, timezone
import sqlite3

from provider_credentials import create_schema, issue_window, rotation_needed
from provider_key_rotation import rotate_active_key


class FakeProvider:
    def __init__(self):
        self.created = []
        self.revoked = []

    def create_key(self, *, expires_at):
        self.created.append(expires_at)
        return "aje-new-key", "plaintext-secret"

    def revoke_key(self, provider_key_id):
        self.revoked.append(provider_key_id)


def test_rotation_promotes_one_global_key_for_12_hours():
    now = datetime(2026, 1, 1, 11, tzinfo=timezone.utc)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    issued, expires = issue_window(now)
    conn.execute(
        """INSERT INTO provider_credentials
           (api_key_encrypted, yandex_key_id, project_id, issued_at, expires_at, status)
           VALUES (?, ?, ?, ?, ?, 'active')""",
        ("old-cipher", "aje-old-key", "project-1", now - timedelta(hours=11), now + timedelta(minutes=30)),
    )
    conn.commit()

    provider = FakeProvider()
    new_id, new_issued, new_expires = rotate_active_key(
        db=conn,
        provider=provider,
        encrypt=lambda value: "enc:" + value,
        old_id=1,
        project_id="project-1",
        old_provider_key_id="aje-old-key",
        now=now,
        commit_before_revoke=True,
    )

    rows = conn.execute(
        "SELECT yandex_key_id, status, expires_at FROM provider_credentials ORDER BY id"
    ).fetchall()
    active = [row for row in rows if row["status"] == "active"]

    assert new_id == "aje-new-key"
    assert new_issued == issued
    assert new_expires == expires
    assert new_expires - new_issued == timedelta(hours=12)
    assert len(active) == 1
    assert active[0]["yandex_key_id"] == "aje-new-key"
    assert provider.revoked == ["aje-old-key"]


def test_rotation_window_starts_one_hour_before_expiry():
    now = datetime(2026, 1, 1, 0, tzinfo=timezone.utc)
    assert rotation_needed(now + timedelta(hours=1), now)
    assert not rotation_needed(now + timedelta(hours=1, seconds=1), now)
