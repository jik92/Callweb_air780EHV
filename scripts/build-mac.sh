#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  print -u2 "请在 Apple Silicon macOS 上编译（M1/M2/M3/M4，arm64）。"
  exit 1
fi
if [[ "${1:-dmg}" != "dmg" && "${1:-dmg}" != "app" ]]; then
  print -u2 "用法：./scripts/build-mac.sh [dmg|app]"
  exit 1
fi
for dependency in uv npm iconutil rg; do
  command -v "$dependency" >/dev/null || { print -u2 "缺少构建工具：$dependency"; exit 1; }
done
[[ -x tools/luatos-cli && -f firmware/LuatOS-SoC_V2052_Air780EHV_13.soc ]] || {
  print -u2 "缺少本项目 tools/luatos-cli 或 V2052 Air780EHV_13 固件。"; exit 1;
}
file tools/luatos-cli | rg -q arm64 || { print -u2 "烧录 CLI 必须为 arm64。"; exit 1; }
uv sync --project backend --group desktop-build --locked
npm ci --prefix frontend
npm ci --prefix desktop
npm run check --prefix frontend
npm run check --prefix desktop
npm run build --prefix frontend
uv run --directory backend --group desktop-build python ../scripts/build-mac-icons.py
iconutil -c icns build/mac-assets/app.iconset -o build/mac-assets/app.icns
uv run --directory backend --group desktop-build pyinstaller \
  --noconfirm --clean --onedir --name air780-backend --target-arch arm64 \
  --distpath ../build/mac-backend --workpath ../build/mac-pyinstaller --specpath ../build/mac-pyinstaller \
  --collect-submodules uvicorn --collect-submodules websockets --collect-submodules app \
  --collect-all soxr --collect-all certifi desktop_entry.py
if [[ "${1:-dmg}" == "app" ]]; then
  npm run pack --prefix desktop
else
  npm run dist --prefix desktop
fi
print "Apple Silicon 应用：$PWD/build/mac/mac-arm64/Air780 Console.app"
if [[ "${1:-dmg}" == "dmg" ]]; then
  app_version="$(node -p 'require("./desktop/package.json").version')"
  print "安装镜像：$PWD/build/mac/Air780-Console-${app_version}-arm64.dmg"
fi
