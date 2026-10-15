from datetime import datetime, timedelta, timezone

from provider_accounts.credentials import create_schema, issue_window, rotation_needed
from provider_accounts.key_rotation import rotate_active_key


class FakeProvider:
    def __init__(self):
        self.created = []
        self.revoked = []

    def create_key(self, *, expires_at):
        self.created.append(expires_at)
        return "aje-new-key", "plaintext-secret"

    def validate_key(self, api_key):
        assert api_key == "plaintext-secret"

    def revoke_key(self, provider_key_id):
        self.revoked.append(provider_key_id)


def test_rotation_promotes_one_global_key_for_12_hours():
    import db
    from provider_accounts.credentials import replace_active_credential, list_provider_credentials

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = db.get_store()
    old = replace_active_credential(
        store,
        "old",
        "project-1",
        lambda value: "cipher",
        "yandex",
        provider_key_id="aje-old-key",
        now=now - timedelta(hours=11),
    )
    provider = FakeProvider()
    new_id, issued, expires = rotate_active_key(
        db=store,
        provider=provider,
        encrypt=lambda value: "enc:" + value,
        old_id=old.id,
        project_id="project-1",
        old_provider_key_id="aje-old-key",
        now=now,
        commit_before_revoke=True,
    )
    active = [row for row in list_provider_credentials(store) if row["status"] == "active"]
    assert new_id == "aje-new-key"
    assert expires - issued == timedelta(hours=12)
    assert len(active) == 1 and active[0]["provider_key_id"] == new_id
    assert provider.revoked == ["aje-old-key"]


def test_rotation_window_starts_one_hour_before_expiry():
    now = datetime(2026, 1, 1, 0, tzinfo=timezone.utc)
    assert rotation_needed(now + timedelta(hours=1), now)
    assert not rotation_needed(now + timedelta(hours=1, seconds=1), now)


class FakeReissueProvider:
    def __init__(self):
        self.reissued = []

    def reissue_key(self, provider_key_id, *, expires_at):
        self.reissued.append((provider_key_id, expires_at))
        return provider_key_id, "cloudru-new-secret"

    def validate_key(self, api_key):
        assert api_key == "cloudru-new-secret"

    def create_key(self, *, expires_at):
        raise AssertionError("Cloud.ru rotation must reissue the existing key")

    def revoke_key(self, provider_key_id):
        raise AssertionError("Cloud.ru reissue must not revoke the same resource")


def test_reissue_rotation_keeps_cloudru_provider_key_id():
    import db
    from provider_accounts.credentials import replace_active_credential, list_provider_credentials

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = db.get_store()
    old = replace_active_credential(
        store,
        "old",
        "",
        lambda value: "cipher",
        "cloudru",
        provider_key_id="cloudru-key",
        now=now - timedelta(hours=11),
    )
    provider = FakeReissueProvider()
    new_id, issued, expires = rotate_active_key(
        db=store,
        provider=provider,
        encrypt=lambda value: "enc:" + value,
        old_id=old.id,
        project_id="",
        old_provider_key_id="cloudru-key",
        now=now,
        provider_name="cloudru",
        reissue_existing=True,
        ttl=timedelta(days=1),
        commit_before_revoke=True,
    )
    assert new_id == "cloudru-key"
    assert expires - issued == timedelta(days=1)
    assert len([row for row in list_provider_credentials(store) if row["status"] == "active"]) == 1
    assert provider.reissued == [("cloudru-key", expires)]
