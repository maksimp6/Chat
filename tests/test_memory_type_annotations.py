"""Contract tests for Memory DB public Python type annotations.

Runtime storage and domain value validation are separate concerns: this test
ensures the public repository interface is parameterized, not typed as Any.
"""

from typing import get_type_hints

from memory_engine import Store
from memory_engine.store import ValueT


def test_repository_contract_is_generic() -> None:
    """Allow separate consumers to bind a value type to the same API shape."""
    assert Store[str] != Store[int]
    assert get_type_hints(Store.get)["return"] == ValueT | None
    assert get_type_hints(Store.set)["value"] is ValueT
    assert get_type_hints(Store.commit)["return"] is int


def test_store_contract_method_parameters_are_explicit() -> None:
    """Names remain strings; the public API has no namespace/key arguments."""
    for method in (Store.get, Store.set):
        hints = get_type_hints(method)
        assert hints["name"] is str
        assert "namespace" not in hints
        assert "key" not in hints
