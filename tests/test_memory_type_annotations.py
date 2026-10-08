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
    assert get_type_hints(Store.delete)["return"] is type(None)


def test_store_contract_method_parameters_are_explicit() -> None:
    """Namespace and key remain strings across typed get/set/delete calls."""
    for method in (Store.get, Store.set, Store.delete):
        hints = get_type_hints(method)
        assert hints["namespace"] is str
        assert hints["key"] is str
