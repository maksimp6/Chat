"""Issue #703 private transport: authenticated opaque checkpoint envelope.

Only synthetic secrets/content are used. These gates exercise public byte APIs;
an independent source review must also verify AES-GCM and HKDF domain separation.
Authenticated encryption does not prove that a downloaded checkpoint is newest:
the workflow must separately validate its committed generation and cursor.
"""

from __future__ import annotations

import copy

import pytest


SECRET = "synthetic-provider-auth-secret-never-a-real-token"
PRIVATE_CONTENT = b"Private goal and conversation: keep all owner approval requirements."
BINDING = {
    "repo_id": 12345,
    "session_id": "62f26d78-5ad7-4e21-a682-f486649c40ba",
    "native_session_id": "dc462c2c-bf29-4cce-8b18-6ba331061f09",
    "schema": 1,
    "generation": 1,
}


@pytest.fixture
def checkpoint():
    from agent_office import claude_checkpoint

    return claude_checkpoint


def _assert_generic_rejection(checkpoint, ciphertext, secret=SECRET, binding=BINDING):
    with pytest.raises(ValueError) as caught:
        checkpoint.open_checkpoint(ciphertext, secret, binding)
    assert str(caught.value) == "checkpoint_invalid"
    assert SECRET not in str(caught.value)
    assert PRIVATE_CONTENT.decode() not in str(caught.value)


def test_authenticated_round_trip_is_opaque_bytes(checkpoint):
    ciphertext = checkpoint.seal_checkpoint(PRIVATE_CONTENT, SECRET, BINDING)
    assert isinstance(ciphertext, bytes)
    assert PRIVATE_CONTENT not in ciphertext
    assert SECRET.encode() not in ciphertext
    assert checkpoint.open_checkpoint(ciphertext, SECRET, BINDING) == PRIVATE_CONTENT


def test_same_input_uses_distinct_random_nonce_each_time(checkpoint):
    first = checkpoint.seal_checkpoint(PRIVATE_CONTENT, SECRET, BINDING)
    second = checkpoint.seal_checkpoint(PRIVATE_CONTENT, SECRET, BINDING)
    assert first != second
    assert checkpoint.open_checkpoint(first, SECRET, BINDING) == PRIVATE_CONTENT
    assert checkpoint.open_checkpoint(second, SECRET, BINDING) == PRIVATE_CONTENT


def test_wrong_secret_cannot_open_checkpoint_or_expose_details(checkpoint):
    ciphertext = checkpoint.seal_checkpoint(PRIVATE_CONTENT, SECRET, BINDING)
    _assert_generic_rejection(checkpoint, ciphertext, secret="different-synthetic-secret")


@pytest.mark.parametrize(
    ("field", "other"),
    [
        ("repo_id", 54321),
        ("session_id", "379eeb46-ef66-4fe3-b36d-aa10363e8552"),
        ("native_session_id", "37ce5742-3563-4ff2-9d16-2bcdb0782d0f"),
        ("schema", 2),
        ("generation", 2),
    ],
)
def test_every_binding_dimension_is_authenticated(checkpoint, field, other):
    ciphertext = checkpoint.seal_checkpoint(PRIVATE_CONTENT, SECRET, BINDING)
    wrong_scope = copy.deepcopy(BINDING)
    wrong_scope[field] = other
    _assert_generic_rejection(checkpoint, ciphertext, binding=wrong_scope)


def test_tamper_or_truncation_is_rejected_without_private_error_text(checkpoint):
    ciphertext = checkpoint.seal_checkpoint(PRIVATE_CONTENT, SECRET, BINDING)
    altered = bytearray(ciphertext)
    altered[-1] ^= 1
    _assert_generic_rejection(checkpoint, bytes(altered))
    _assert_generic_rejection(checkpoint, ciphertext[:-1])
    _assert_generic_rejection(checkpoint, b"")


@pytest.mark.parametrize("invalid", [None, "text-is-not-encrypted-bytes", 12, {}])
def test_nonbyte_ciphertext_is_generic_rejection(checkpoint, invalid):
    _assert_generic_rejection(checkpoint, invalid)


@pytest.mark.parametrize("invalid", [None, "", 7, b"bytes-not-a-secret-string"])
def test_secret_type_or_empty_secret_is_rejected(checkpoint, invalid):
    with pytest.raises(ValueError, match="^checkpoint_invalid$"):
        checkpoint.seal_checkpoint(PRIVATE_CONTENT, invalid, BINDING)


def test_extra_or_missing_scope_fields_are_rejected(checkpoint):
    with_unknown = dict(BINDING, filesystem_path="../../credentials.json")
    without_generation = {key: value for key, value in BINDING.items() if key != "generation"}
    for binding in (with_unknown, without_generation):
        with pytest.raises(ValueError, match="^checkpoint_invalid$"):
            checkpoint.seal_checkpoint(PRIVATE_CONTENT, SECRET, binding)


def test_checkpoint_size_is_bounded_before_encryption_and_decryption(checkpoint):
    admitted = b"x" * (16 * 1024 * 1024)
    ciphertext = checkpoint.seal_checkpoint(admitted, SECRET, BINDING)
    assert checkpoint.open_checkpoint(ciphertext, SECRET, BINDING) == admitted
    too_large = b"x" * (16 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="^checkpoint_(invalid|too_large)$"):
        checkpoint.seal_checkpoint(too_large, SECRET, BINDING)
    with pytest.raises(ValueError, match="^checkpoint_(invalid|too_large)$"):
        checkpoint.open_checkpoint(too_large, SECRET, BINDING)
