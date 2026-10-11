#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
if [[ "${1:-start}" == "start" || "${1:-start}" == "restart" ]]; then
  uv sync --project agent
fi
uv run --project agent python scripts/manage_agent.py "${@:-start}"
