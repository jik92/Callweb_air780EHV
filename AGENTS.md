# Air780EHV 开发约定

本文件适用于整个项目；嵌套目录中更具体的 `AGENTS.md` 优先。用户明确指令优先于本文。格式遵循 [AGENTS.md](https://agents.md/)。

## 项目与目录

这是 macOS 上的 Air780EHV / LuatOS 开发环境及本机设备控制台。

- `lua/`：设备业务脚本，当前应用版本以 `lua/main.lua` 为准；已有 SIM 查询、短信收发、通话播报及录音功能。
- `backend/app/`：FastAPI + pyserial 串口桥接，uv 管理依赖，Ruff 格式化和检查，SQLite 保存短信。
- `backend/tests/`：协议、短信请求去重、服务状态及 WebSocket 测试。
- `frontend/src/`：React + TypeScript + Vite、TanStack Query / Router、Ant Design、Tailwind CSS 4。
- `scripts/`：后台进程管理、检查、编译、烧录和串口工具。
- `sdk/`、`tools/`、`firmware/`：官方 SDK、CLI 与固件，不随意修改供应商代码。
- `data/`、`logs/`、`build/`：本机运行数据、日志与构建产物，不提交个人 SIM 信息、短信或录音。

## 启动与端口

从项目根目录执行：

```sh
./scripts/dev-console.sh start
./scripts/dev-console.sh status
./scripts/dev-console.sh restart
./scripts/dev-console.sh stop
```

前端为 `http://127.0.0.1:9011`，后端为 `http://127.0.0.1:9010`，WebSocket 路径 `/ws`。用户要求本项目使用 **9xxx 端口**。8000、5173 等其他开发端口可能由用户项目占用，不接管、不停止。后台进程通过管理脚本按 PID 和启动时间识别，禁止用宽泛的 `pkill` 清理进程。

控制台无需登录或用户管理，默认仅监听本机。前端开发服务器代理 `/api` 与 `/ws` 到 9010；修改端口时同步代理、后端 Origin 白名单、管理脚本及文档。

## 前端样式：必须使用 Tailwind

- 新增或修改界面样式全部使用 Tailwind utility class，通过 `className` / Ant Design `classNames` 传入。
- **禁止手写原生 CSS**：不得新增 CSS / SCSS / Sass / Less / Stylus 样式、CSS Modules、选择器规则、`@apply`、自定义 `@layer` 样式或 `<style>` 标签。
- 禁止 React `style` / `styles` 属性、内联样式对象和 CSS-in-JS（如 styled-components、Emotion）。尺寸、颜色、布局、响应式、交互状态均用 Tailwind。
- `frontend/src/tailwind.css` 是唯一样式入口，内容只能为 `@import "tailwindcss";`；这是 Tailwind 编译入口，不得追加手写声明。
- 可以使用 Ant Design 自带组件样式及 `ConfigProvider` 的主题 token。组件内部生成的样式、Tailwind 编译产物不属于手写业务 CSS。
- 动态样式选择完整且静态可扫描的类名，如 `active ? 'bg-blue-600' : 'bg-slate-200'`；禁止拼接 `h-[${height}px]`。数据驱动高度等使用明确的类名映射。
- 主页面框架使用 Ant Design `Layout / Sider / Header / Content / Footer`，保留可折叠菜单、面包屑以及窄屏自动收起导航。
- 保持内容区双栏工作台风格：设备和 SIM 信息在左，短信记录及编辑区在右；窄屏上下排列。保持焦点可见、减少动态效果偏好和无横向溢出。
- Ant Design 的样式覆盖优先使用主题 token；必要时使用 Tailwind 的重要修饰符或任意变体。
- `npm run check --prefix frontend` 包含样式约束检查，禁止绕过或删除该检查来引入手写样式。

## 代码与验证

Python 依赖使用 uv，前端依赖使用 npm；修改依赖时同步提交对应锁文件。前端保留 TypeScript 严格检查、TanStack 的状态与路由架构，业务通信集中在 `frontend/src/connection.ts`。Python 使用 Ruff，前端使用 Prettier。

```sh
# 全部控制台检查：Ruff、后端测试、样式与格式检查、TypeScript/Vite 构建
./scripts/check-console.sh

# 前端单独格式化、检查、构建
npm run format --prefix frontend
npm run check --prefix frontend
npm run build --prefix frontend

# 设备 Lua 编译检查（修改设备脚本时）
./scripts/build.sh
```

涉及界面变更需检查桌面和窄屏显示，以及现有筛选、搜索、表单与 WebSocket 状态。构建不能替代实机验证；报告中区分模拟测试、读取状态和实际发送/通话结果。

## 设备通信与数据保护

- 串口路径以实际枚举为准；当前 USB VUART 常见后缀为 `...17`，日志口为 `...13`，下载口为 `...11`。不要将诊断口当成应用协议口。
- 同一应用串口只能由一个进程读取。后台运行时不要同时运行独立查询、发送、监听或烧录工具。
- 保留已有短信收件箱、SQLite 数据、发送请求 ID 去重和重启恢复逻辑；不得为测试清空真实数据。
- WebSocket 可以自动重连；**短信和拨号命令不得自动重试或在上电时自动执行**。修改界面或执行自动化检查时不实际发送短信或拨打电话。
- 实际短信、电话及播报使用用户已明确授权的号码与内容。测试默认使用模拟设备；新增真实发送须有对应任务授权。
- `submitted` 只表示模块报告提交短信中心，不能显示成手机已收到；超时、断线或服务重启导致的未知结果保留为 `unknown`。
- SIM 未返回本机号码时显示未提供；不得由 ICCID 或 IMSI 推算号码。
- 避免在提交内容或开发约定中写入真实 ICCID、IMSI、短信内容和录音。相关本地目录维持忽略规则。
- 样式和文档修改无需烧录设备。涉及烧录时先检查官方固件、当前版本和数据备份，再使用已有脚本。
