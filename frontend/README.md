# Air780 设备后台

React + TypeScript + Vite + TanStack Query / Router + Ant Design + Tailwind CSS 4。
采用 Ant Design Layout / Sider / Header / Content / Footer 标准框架，带可折叠菜单和面包屑。
菜单支持设备总览、全部短信、收件箱、已发送，和短信筛选状态同步；窄屏自动收起导航。
内容为双栏工作台：左侧设备、信号、SIM 信息；右侧收件箱/已发送记录及发送、回复。
不含登录和用户管理；窄屏自动改为上下排列。

```sh
npm ci
npm run dev
npm run build
```

开发地址：`http://127.0.0.1:9011`，需要先启动 9010 端口的 FastAPI 服务。
HTTP 获取初始快照；之后用 WebSocket 双向通讯，TanStack Query 维护服务状态缓存。
浏览器 WS 断线自动重连，20 秒心跳；短信 Mutation 不自动重试。
已发送短信会显示模块执行状态，未知结果不会误标为成功。

样式统一使用 Tailwind utility class，禁止手写原生 CSS 和内联 `style`。
`src/tailwind.css` 只保留 Tailwind 导入指令；Ant Design 使用主题 token 和自带样式。
`npm run check` 自动检查样式约束及格式；完整开发约定见 [AGENTS.md](../AGENTS.md)。
