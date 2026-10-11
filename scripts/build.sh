#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
mkdir -p build
tools/luatos-cli project analyze --soc firmware/LuatOS-SoC_V2052_Air780EHV_13.soc
tools/luatos-cli build filesystem --src lua --luac --output build/script.bin --max-size-kb 512
