import json
import os

import pytest

from secret_store.core import SecretRef
from secret_store.file_alias_store import FileSecretAliasStore
from secret_store.manager import SecretAlias


def entry(alias="github", version="v1"):
    return SecretAlias(
        alias=alias,
        ref=SecretRef(
            provider="cloudru",
            secret_id="secret-1",
            version_id=version,
            purpose="github",
        ),
        allowed_purposes=frozenset({"browser.password", "rdc.git"}),
    )


def test_file_alias_store_survives_reopen_and_contains_no_plaintext(tmp_path):
    path = tmp_path / "aliases.json"
    store = FileSecretAliasStore(path)
    store.put(entry())

    reopened = FileSecretAliasStore(path)
    assert reopened.get("github") == entry()
    assert reopened.list() == (entry(),)
    text = path.read_text(encoding="utf-8")
    assert "synthetic-secret" not in text
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_file_alias_store_rotation_updates_reference_atomically(tmp_path):
    path = tmp_path / "aliases.json"
    store = FileSecretAliasStore(path)
    store.put(entry(version="v1"))
    store.put(entry(version="v2"))

    assert store.get("github") == entry(version="v2")
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 1


def test_file_alias_store_delete_is_durable(tmp_path):
    path = tmp_path / "aliases.json"
    store = FileSecretAliasStore(path)
    store.put(entry())
    store.delete("github")

    assert FileSecretAliasStore(path).list() == ()


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "{}",
        '{"schema":1,"aliases":"wrong"}',
        '{"schema":1,"aliases":[{"alias":"../bad"}]}',
    ],
)
def test_file_alias_store_corruption_fails_closed(tmp_path, payload):
    path = tmp_path / "aliases.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match="invalid secret alias store"):
        FileSecretAliasStore(path).list()
