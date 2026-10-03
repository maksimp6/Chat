#!/usr/bin/env bash
set -euo pipefail

mkdir -p profiling
target="${1:-tests/test_chat_api.py}"
shift || true

python -m pyinstrument \
  --renderer json \
  --outfile profiling/pyinstrument.json \
  -m pytest -q "$target" "$@"

python - <<'PY'
import json
from pathlib import Path
from pyinstrument import Profiler

# Presence/parseability check keeps the artifact contract deterministic.
path = Path("profiling/pyinstrument.json")
json.loads(path.read_text(encoding="utf-8"))
print(path)
PY
