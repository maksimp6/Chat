"""Regression for retiring the Alice class names in agent_runtime/cli.py (#1316).

The class names are internal, so they change. The wire model key and the ALICE_*
environment variables are compatibility contracts, so they must not change.
"""

import requests

from agent_runtime import cli


class _UnreachableSession:
    def request(self, *args, **kwargs):
        raise requests.ConnectionError("refused")


def test_cli_classes_use_neutral_names():
    assert issubclass(cli.CliError, RuntimeError)
    assert isinstance(cli.CliClient, type)
    assert not hasattr(cli, "AliceCliError")
    assert not hasattr(cli, "AliceCliClient")


def test_cli_error_is_raised_for_unreachable_backend_without_secrets():
    client = cli.CliClient(
        base_url="http://backend.test", short_token="secret-token", session=_UnreachableSession()
    )
    try:
        client.create_conversation()
    except cli.CliError as exc:
        assert str(exc) == "Alice Pro backend is unavailable"
        assert "secret-token" not in str(exc)
    else:
        raise AssertionError("expected CliError")


def test_default_model_key_is_unchanged_for_the_backend_contract():
    assert cli.DEFAULT_MODEL == "aliceai-llm"


def test_alice_environment_variables_still_configure_the_client(monkeypatch):
    monkeypatch.setenv("ALICE_BASE_URL", "http://env.test/")
    monkeypatch.setenv("ALICE_SHORT_TOKEN", "  env-token  ")
    monkeypatch.setenv("ALICE_MODEL", "env-model")

    client = cli.CliClient()

    assert client.base_url == "http://env.test"
    assert client.short_token == "env-token"
    assert client.model == "env-model"
