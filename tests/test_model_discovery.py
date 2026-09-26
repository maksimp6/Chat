from model_discovery import ModelDiscovery, ModelDiscoveryError


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("provider request failed")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        return self.response


def test_discovers_and_normalizes_models():
    session = FakeSession(
        FakeResponse(
            {
                "data": [
                    {"id": "gpt://folder/qwen3.6-35b-a3b/latest"},
                    {"id": "gpt-oss-120b", "name": "GPT OSS 120B"},
                ]
            }
        )
    )
    discovery = ModelDiscovery(
        base_url="https://provider.example/v1",
        api_key="secret",
        session=session,
        ttl_seconds=300,
    )

    catalog = discovery.get_models()

    assert "qwen3.6-35b-a3b" in catalog["text"]
    assert catalog["text"]["qwen3.6-35b-a3b"]["type"] == "text"
    assert catalog["text"]["gpt-oss-120b"]["name"] == "GPT OSS 120B"
    assert catalog["voice"]


def test_discovery_is_cached_until_explicit_refresh():
    session = FakeSession(FakeResponse({"data": [{"id": "model-a"}]}))
    discovery = ModelDiscovery(
        base_url="https://provider.example/v1",
        api_key="secret",
        session=session,
        ttl_seconds=300,
    )

    discovery.get_models()
    discovery.get_models()
    discovery.get_models(force_refresh=True)

    assert len(session.calls) == 2


def test_empty_provider_response_is_rejected():
    session = FakeSession(FakeResponse({"data": []}))
    discovery = ModelDiscovery(
        base_url="https://provider.example/v1",
        api_key="secret",
        session=session,
    )

    try:
        discovery.get_models()
    except ModelDiscoveryError:
        pass
    else:
        raise AssertionError("empty model catalog must fail")


def test_missing_credentials_fail_before_network_request():
    session = FakeSession(FakeResponse({"data": [{"id": "model-a"}]}))
    discovery = ModelDiscovery(
        base_url="https://provider.example/v1",
        api_key=None,
        session=session,
    )

    try:
        discovery.get_models()
    except ModelDiscoveryError:
        pass
    else:
        raise AssertionError("missing credentials must fail")

    assert session.calls == []


def test_api_key_is_sent_only_as_authorization_header():
    session = FakeSession(FakeResponse({"data": [{"id": "model-a"}]}))
    discovery = ModelDiscovery(
        base_url="https://provider.example/v1",
        api_key="secret",
        session=session,
    )

    discovery.get_models()

    _, headers, _ = session.calls[0]
    assert headers == {"Authorization": "Api-Key secret"}
