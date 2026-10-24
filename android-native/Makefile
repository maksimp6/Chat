# Цели сборки и проверки нативного лаунчера. Запуск на телефоне в Termux.
.PHONY: build test bench

build:
	bash tools/build-full.sh

test:
	bash tools/test.sh
	bash tools/elf/check.sh
	bash tools/lint-perf.sh

bench:
	bash tools/bench.sh
