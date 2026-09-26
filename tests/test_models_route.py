import app as app_module


class FakeDiscovery:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.refresh_values = []

    def get_models(self, *, force_refresh=False):
        self.refresh_values.append(force_refresh)
        if self.error:
            raise self.error
        return self.result


def test_models_route_returns_provider_catalog(monkeypatch):
    discovery = FakeDiscovery(
        {
            "text": {"provider-model": {"name": "Provider Model", "type": "text"}},
            "voice": {},
        }
    )
    monkeypatch.setattr(app_module, "MODEL_DISCOVERY", discovery)

    response = app_module.app.test_client().get("/api/models?refresh=1")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["degraded"] is False
    assert "provider-model" in payload["text"]
    assert discovery.refresh_values == [True]


def test_models_route_degrades_to_static_catalog(monkeypatch):
    discovery = FakeDiscovery(error=app_module.ModelDiscoveryError("unavailable"))
    monkeypatch.setattr(app_module, "MODEL_DISCOVERY", discovery)

    response = app_module.app.test_client().get("/api/models")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["degraded"] is True
    assert payload["error"] == "model_discovery_unavailable"
    assert "aliceai-llm" in payload["text"]
