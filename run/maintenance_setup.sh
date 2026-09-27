#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/codex_env.sh"
install_project_deps
install_cloud_cli
printf 'Codex cached environment refreshed.\n'
