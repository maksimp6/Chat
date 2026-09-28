import pytest

from browser.adapters import BrowserAdapterRegistry, register_browser_tools
from browser.capabilities import BrowserAction
from universal_tool_platform import UniversalToolExecutor


class FakeAdapter:
    def __init__(self, result=None, error=None):
        self.result = result or {"success": True, "data": {"text": "ok"}}
        self.error = error

    def execute(self, action):
        if self.error:
            raise self.error
        return self.result


class FakeToolRegistry:
    def __init__(self):
        self.definitions = {}
        self.functions = {}

    def register(self, category, name, cfg):
        self.definitions[name] = cfg
        self.functions[name] = cfg["func"]

    def get_universal_definition(self, name):
        return self.definitions.get(name)

    def execute(self, name, arguments, context=None):
        return self.functions[name](arguments, context or {})


def test_registry_returns_unavailable_for_unregistered_capability():
    result = BrowserAdapterRegistry().execute(BrowserAction("browser_local", "inspect", "page"))

    assert result["success"] is False
    assert result["metadata"]["phase"] == "adapter_unavailable"


def test_registry_validates_actions_and_capability_names():
    registry = BrowserAdapterRegistry()

    invalid_action = registry.execute(BrowserAction("browser_local", "submit", "form"))
    invalid_capability = registry.execute(BrowserAction("missing", "click", "button"))

    assert invalid_action["metadata"]["phase"] == "validation"
    assert invalid_capability["metadata"]["phase"] == "validation"


def test_registry_rejects_unknown_registration():
    with pytest.raises(ValueError, match="unknown browser capability"):
        BrowserAdapterRegistry().register("missing", FakeAdapter())


def test_registry_registers_known_adapter():
    registry = BrowserAdapterRegistry()
    registry.register("browser_local", FakeAdapter({"success": True, "data": {"ok": True}}))

    result = registry.execute(BrowserAction("browser_local", "inspect", "page"))

    assert result["success"] is True
    assert result["data"] == {"ok": True}


def test_registry_sanitizes_successful_result():
    registry = BrowserAdapterRegistry(
        {"browser_local": FakeAdapter({"success": True, "data": {"cookie": "secret"}})}
    )

    result = registry.execute(BrowserAction("browser_local", "inspect", "page"))

    assert result["success"] is True
    assert result["data"]["cookie"] == "<redacted>"


def test_registry_classifies_adapter_errors_and_bad_results():
    failing = BrowserAdapterRegistry({"browser_cloud": FakeAdapter(error=RuntimeError("offline"))})
    invalid = BrowserAdapterRegistry({"browser_local": FakeAdapter(result=["bad"])})

    failure = failing.execute(BrowserAction("browser_cloud", "navigate", "url"))
    bad_result = invalid.execute(BrowserAction("browser_local", "inspect", "page"))

    assert failure["metadata"]["phase"] == "execution"
    assert "offline" in failure["error"]
    assert bad_result["metadata"]["phase"] == "result_validation"


def test_register_browser_tools_runs_through_universal_executor():
    adapters = BrowserAdapterRegistry(
        {"browser_local": FakeAdapter({"success": True, "data": {"title": "Alice"}})}
    )
    registry = FakeToolRegistry()
    register_browser_tools(registry, adapters)

    result = UniversalToolExecutor(registry).execute(
        {
            "tool_name": "browser_local",
            "arguments": {"action": "inspect", "target": "page", "value": None},
            "transport": "local_agent",
            "approved": True,
        }
    )

    assert result["success"] is True
    assert result["data"] == {"title": "Alice"}
    assert set(registry.definitions) == {"browser_cloud", "browser_local"}
