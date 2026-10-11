#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
tools/luatos-cli flash script \
  --soc firmware/LuatOS-SoC_V2052_Air780EHV_13.soc \
  --port "${1:-auto}" --script lua
