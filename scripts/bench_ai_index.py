#!/usr/bin/env python3
"""Benchmark: full index build vs repeated query on a fixed fixture.

Runs REPETITIONS rounds of each operation and prints mean wall-clock time.
The fixture is a small but structurally representative Python package written
to a temporary directory so results are stable across repository states.

Usage:
    python scripts/bench_ai_index.py
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from scripts.build_ai_index import build_index, query_affected

REPETITIONS = 20

_FIXTURE: dict[str, str] = {
    "pkg/__init__.py": "",
    "pkg/models.py": (
        "class User:\n"
        "    def __init__(self, name: str) -> None:\n"
        "        self.name = name\n"
        "\n"
        "class Product:\n"
        "    def __init__(self, sku: str) -> None:\n"
        "        self.sku = sku\n"
    ),
    # service.py imports models.py directly so it is a first-order reverse dep
    "pkg/service.py": (
        "from pkg.models import User, Product\n"
        "\n"
        "def order(uid: int, sku: str) -> dict:\n"
        "    return {'user': User(str(uid)).name, 'sku': Product(sku).sku}\n"
    ),
    "pkg/utils.py": (
        "def slugify(text: str) -> str:\n"
        "    return text.lower().replace(' ', '-')\n"
    ),
    "tests/__init__.py": "",
    "tests/test_models.py": (
        "from pkg.models import User, Product\n"
        "\n"
        "def test_user_name() -> None:\n"
        "    assert User('alice').name == 'alice'\n"
        "\n"
        "def test_product_sku() -> None:\n"
        "    assert Product('X1').sku == 'X1'\n"
    ),
    "tests/test_service.py": (
        "from pkg.service import order\n"
        "\n"
        "def test_order() -> None:\n"
        "    result = order(1, 'X1')\n"
        "    assert result['sku'] == 'X1'\n"
    ),
    "tests/test_utils.py": (
        "from pkg.utils import slugify\n"
        "\n"
        "def test_slugify() -> None:\n"
        "    assert slugify('Hello World') == 'hello-world'\n"
    ),
}

_CHANGED = ["pkg/models.py"]


def _create_fixture(root: Path) -> None:
    for rel, source in _FIXTURE.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _mean_ms(times: list[float]) -> float:
    return sum(times) / len(times) * 1000


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _create_fixture(root)

        # Warm up (discarded)
        build_index(root)

        # Full build benchmark
        full_times: list[float] = []
        for _ in range(REPETITIONS):
            t0 = time.perf_counter()
            build_index(root)
            full_times.append(time.perf_counter() - t0)

        # Build index once, then benchmark repeated queries
        index = build_index(root)
        query_times: list[float] = []
        for _ in range(REPETITIONS):
            t0 = time.perf_counter()
            query_affected(index, _CHANGED)
            query_times.append(time.perf_counter() - t0)

        full_ms = _mean_ms(full_times)
        query_ms = _mean_ms(query_times)
        speedup = full_ms / query_ms if query_ms > 0 else float("inf")

        # Correctness check
        result = query_affected(index, _CHANGED)
        assert "pkg.models" in result["affected_modules"], "direct module missing"
        assert "pkg.service" in result["affected_modules"], "first-order reverse dep missing"
        assert "tests/test_models.py" in result["affected_tests"], "direct test missing"
        assert "tests/test_service.py" in result["affected_tests"], "indirect test missing"

        files = result["changed_files"]
        assert files[0]["in_index"] is True
        assert len(files[0]["sha256_in_index"]) == 64

        print(f"fixture: {len(_FIXTURE)} files, {REPETITIONS} repetitions each")
        print(f"full build (mean): {full_ms:.2f} ms")
        print(f"query_affected (mean): {query_ms:.4f} ms")
        print(f"speedup: {speedup:.0f}x")
        print("correctness: ok")


if __name__ == "__main__":
    main()
