#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
tools/luatos-cli flash run \
  --soc firmware/LuatOS-SoC_V2052_Air780EHV_13.soc \
  --port "${1:-auto}" --script lua --tail-log-secs 15
