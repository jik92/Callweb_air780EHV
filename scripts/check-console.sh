#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
uv run --directory backend ruff check . ../scripts/manage_console.py
uv run --directory backend ruff format --check . ../scripts/manage_console.py
uv run --directory backend pytest
npm run check --prefix frontend
npm run build --prefix frontend
