"""Dependency-free 2-second budget for standalone MemoryStore smoke contracts."""
import sys
from pathlib import Path
from time import perf_counter
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
start = perf_counter()
suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_memory_fast_core_contracts")
result = unittest.TextTestRunner(verbosity=2).run(suite)
elapsed = perf_counter() - start
print(f"Memory core fast: {result.testsRun} tests / {elapsed:.3f}s (limit 2.000s)", flush=True)
raise SystemExit(0 if result.wasSuccessful() and result.testsRun >= 5 and elapsed <= 2.0 else 1)
