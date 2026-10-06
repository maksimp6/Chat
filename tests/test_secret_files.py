import os

import pytest

from secret_store.fake import FakeSecretResolver
from secret_store.fake_admin import FakeSecretAdminBackend
from secret_store.files import import_secret_file, materialize_secret_file


def test_import_returns_reference_without_plaintext(tmp_path):
    source = tmp_path / "input.txt"
    source.write_text("synthetic-secret", encoding="utf-8")
    admin = FakeSecretAdminBackend()

    result = import_secret_file(source, purpose="rdc-git", admin=admin)

    assert result.ref.purpose == "rdc-git"
    assert "synthetic-secret" not in repr(result)
    assert admin.resolve_for_test(result.ref) == "synthetic-secret"


def test_materialized_secret_is_0600_and_removed_after_success(tmp_path):
    resolver = FakeSecretResolver({"secret-1": "synthetic-secret"})
    ref = resolver.ref("secret-1")

    with materialize_secret_file(resolver, ref, directory=tmp_path) as path:
        assert path.read_text(encoding="utf-8") == "synthetic-secret"
        assert os.stat(path).st_mode & 0o777 == 0o600
        materialized = path

    assert not materialized.exists()


def test_materialized_secret_is_removed_after_consumer_failure(tmp_path):
    resolver = FakeSecretResolver({"secret-1": "synthetic-secret"})
    ref = resolver.ref("secret-1")

    with pytest.raises(RuntimeError):
        with materialize_secret_file(resolver, ref, directory=tmp_path) as path:
            materialized = path
            raise RuntimeError("consumer failed")

    assert not materialized.exists()


def test_empty_import_fails_closed(tmp_path):
    source = tmp_path / "empty.txt"
    source.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="non-empty"):
        import_secret_file(source, purpose="rdc-git", admin=FakeSecretAdminBackend())
