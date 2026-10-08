PYTHON ?= python
DIST := dist

.PHONY: build test clean

build:
	$(PYTHON) -m pip wheel --no-deps --wheel-dir $(DIST) .

test:
	$(PYTHON) -m pytest -q ../tests/test_memory_explicit_commit_contract.py ../tests/test_memory_v1_acceptance.py ../tests/test_memory_store.py ../tests/test_memory_standalone_boundary.py ../tests/test_memory_makefile.py ../tests/test_memory_package_sync.py

clean:
	rm -rf $(DIST)
