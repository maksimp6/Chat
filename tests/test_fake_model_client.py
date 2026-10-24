"""The test double answers like the Responses client and never reaches the network."""

import requests

from tests.fake_model import FakeModelClient, install
from yandex_client_modules.client import YandexResponsesClient


def test_reply_is_read_by_the_production_parsers():
    fake = FakeModelClient("Привет")

    data = fake.ask("hi", "yandexgpt-5.1")

    assert fake.extract_text(data) == "Привет"
    usage = fake.extract_usage(data)
    assert usage["output_tokens"] == len("Привет")
    assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]


def test_calls_record_a_copy_of_the_params():
    fake = FakeModelClient()
    params = {"instructions": "be brief"}

    fake.ask("hi", "logos-runtime", "conv-1", params)
    params["instructions"] = "changed later"

    assert fake.calls == [
        {
            "message": "hi",
            "model_key": "logos-runtime",
            "conversation_id": "conv-1",
            "params": {"instructions": "be brief"},
        }
    ]


def test_installed_fake_serves_the_real_client_class_without_network(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("the test double must not reach the network")

    monkeypatch.setattr(requests.Session, "request", no_network)
    fake = install(monkeypatch, FakeModelClient("ok"))
    client = object.__new__(YandexResponsesClient)

    data = client.ask("hi", "yandexgpt-5.1")

    assert client.extract_text(data) == "ok"
    assert [call["model_key"] for call in fake.calls] == ["yandexgpt-5.1"]
