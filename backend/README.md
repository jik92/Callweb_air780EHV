# 本地串口桥接服务

使用 Python + FastAPI + pyserial；依赖与工具由 uv 管理，Ruff 检查/格式化。
仅监听本机，不含用户、登录或权限管理。一个服务进程独占 USB VUART，
多个浏览器通过同一个 WebSocket 获得设备状态与短信记录。

在项目根目录运行：

```sh
uv sync --project backend
uv run --directory backend uvicorn app.main:app --host 127.0.0.1 --port 9010
```

`AIR780_SERIAL_PORT` 可指定设备串口，默认自动选择 `19D1` 模组的 `cu.*17` 应用口。
`AIR780_DATA_DIR` 可指定存储目录，默认项目根目录的 `data/`。
保持单 worker；文件锁会阻止同一数据目录的另一个桥接进程启动。
服务运行期间不要同时启动原来的串口查询、监听或烧录脚本。

`set_sim_number` / `clear_sim_number` 是本机配置命令，通过 `/ws` 提交，
以 `sim_id` 携带界面所见 ICCID，设置命令通过 `to` 传入确认的号码。
只允许设置当前就绪 SIM，ICCID 变化时拒绝旧页面的请求；配置写入 SQLite
`sim_numbers` 表，不发送到设备，不产生短信或电话。快照中的 `mobile.number_source`
区分 `sim`、`manual`、`unavailable`，SIM 返回的号码优先于人工标注。
使用 CLI 烧录时先停止服务，烧录后重新启动。
使用 Chrome「硬件刷写」菜单时保持服务运行，由页面暂停和恢复桥接。

## 接口

- `GET /api/health`：服务及设备连接状态。
- `GET /api/state`：当前状态和最近 500 条短信记录。
- `GET /api/ports`：可用 macOS 串口列表。
- `WS /ws`：状态推送及设备命令。
- `GET /docs`：HTTP 接口说明。

前端由 Vite 代理 API 和 WebSocket 到 9010。
构建 `frontend/dist` 后，后端也可直接通过 `http://127.0.0.1:9010/` 提供页面。
WebSocket 接受 `localhost`、`127.0.0.1` 的 9011/9010 页面 Origin。
非浏览器客户端也需设置匹配的 Origin。

服务端建立 WS 后立即发送完整快照，并在设备事件或记录变化时发送新快照：

```json
{"type":"snapshot","bridge":{"connected":true,"ready":true,"port":"/dev/cu.usbmodem...17","last_seen":1791690000,"error":null},"device":{"type":"status_response","mobile":{}},"messages":[]}
```

客户端命令都需要 UUID `request_id`：

```json
{"action":"get_status","request_id":"5d1999ac-f8cd-49c8-a4b7-4d62b3b82fce"}
{"action":"get_inbox","request_id":"57a601bf-e482-49f9-a50d-4ad9531eb523"}
{"action":"send_sms","request_id":"6c80c969-d571-47e2-bddf-fec63d7a48db","to":"+15555550100","content":"你好"}
{"action":"ping","request_id":"831109a2-9416-4c48-9f32-de6f2a6caeb4"}
```

返回 `command_ack` / `command_error`（含同一个 request_id）；心跳返回 `pong`。
`command_ack` 只表示服务接收请求，短信执行结果在后续快照的 messages 中。

短信状态：`queued` 等待发送，`sending` 等待模块结果，`submitted` 提交短信中心成功，
`failed` 明确失败，`unknown` 发送结果未知，`received` 已接收。
`submitted` 不代表手机送达确认。未知状态不自动重试，以防重复发送。
同一个 request_id 即使服务重启也不会重复下发，相同 ID 不允许更改号码或内容。

串口中断自动重连，但不重放发送命令；设备信息每 10 秒查询，收件箱每 60 秒同步。
来自串口的即时 `incoming_sms` 事件会立即保存并推送。收件箱重复同步按
SIM ICCID + 发件号码 + 内容 + 设备时间去重；SQLite 存储路径 `data/console.sqlite3`。

设备固件当前最多保存最近 20 条接收短信，本机服务保留已同步的历史。
服务关闭期间超出设备 20 条容量的旧消息不能保证补读。
短信内容限制为 512 UTF-8 字节；为兼容原有固件分隔协议，拒绝包含帧分隔符的内容。

## 检查

```sh
uv run --directory backend ruff check .
uv run --directory backend ruff format --check .
uv run --directory backend pytest
```

自动测试使用模拟设备，不向真实号码发短信；覆盖帧分片/中文、损坏帧恢复、
多客户端 WS 推送、请求去重、重启后的未知状态、输入校验和页面 Origin 限制。

## 实时通话验证通道

每个有效拨号请求先写入 SQLite `calls` 表再交给设备，失败和未接通也保留记录。
请求 ID 跨服务重启去重，不重拨旧请求；中断中的记录标为 `unknown`。
`GET /api/calls` 返回最近 500 条记录，保存的历史不因显示上限删除。
`GET /api/calls/{UUID}/recording` 在通话结束后提供支持 Range 的 WAV 回放/下载。
接通后的本机收发 PCM 分别写入 `data/calls/{UUID}/up.wav` 和 `down.wav`，
按本机时间补齐间隔；首次回放生成 `both.wav`，左声道网页端、右声道手机端。
仅保存实际收到的音频，网络或串口丢帧、录音写入错误可能造成不完整录音；
录音单路限长 100 秒，历史版本的电话不能补录。本机拨号提示音不进入录音。
以上文件与数据库保持在 Git 忽略目录中。

`/ws/call` 独立于短信通道，控制命令 `call_dial`、`call_hangup` 和 `ping` 为 JSON；
使用 UUID `request_id`，挂断附带当前 `call_id`。通话命令不自动重发。
音频仅发给发起通话的连接，其他页面只能看状态。
浏览器上行二进制帧为 `<I` 序号 + S16LE PCM；下行为 `<HI` 采样率和序号 + PCM。
USB 上行采用带 UUID 和长度的 `PCM_START:` 帧，下行采用 `AUD_START:` 帧，
和已有 JSON 命令共享单一串口读取进程。音频队列有限，拥塞丢弃旧帧。
`GET /api/call` 返回实时通话状态、上下行字节数及设备写入统计。
页面断线请求挂断；设备上行输入超时 5 秒为兜底。实时通话无固定 90 秒强制结束限制，
连接、接听及挂断等状态长时间未确认时保留未知结果；TTS 播报任务仍有时限。

## Agent配置与音频

`GET /api/agent` 读取脱敏配置；`PUT /api/agent` 保存配置与 AGENT.md；
`POST /api/agent/test` 验证已保存的 Realtime连接。写入与验证要求控制台 Origin。
Key不返回，包含Key的校验错误也不回显输入值。配置保存在 `data/agent/`，不提交。

`/ws/call` 增加 `call_agent_answer` 命令，参数 `request_id`、`call_id`。
Realtime预连接期间状态为 `agent_connecting`，成功才向模块发送接听命令；
失败恢复为 `incoming`。Agent通话状态携带 `mode=agent` 和 `agent_status`。
准备连接时页面断开或来电结束不会继续接听。运行时错误停止音频并请求挂断。
使用 numpy / soxr 重采样，websockets连接所选服务，无自动重连或命令重放。

## Chrome 刷写文件准备与桥接控制

用户操作步骤见 [主文档：Chrome 直接串口刷写](../README.md#chrome-直接串口刷写)。
硬件写入由 Chrome 直接执行，后端准备接口不打开设备串口。

| 接口 | 用途 |
| --- | --- |
| `POST /api/flash/prepare` | JSON `{"mode":"script"}` 或 `{"mode":"full"}`，解包固定固件、编译当前 `lua/` 项目，返回分区数据与 SHA-256 |
| `GET /api/flash/bridge` | 返回当前 `{"paused":true}` 或 `{"paused":false}` |
| `POST /api/flash/bridge/pause` | 检查设备操作是否繁忙，暂停串口线程，等待端口真正释放 |
| `POST /api/flash/bridge/resume` | 恢复串口自动发现和连接，等待正常运行的设备回应 |

POST 接口要求匹配控制台的 Origin。固件固定为
`firmware/LuatOS-SoC_V2052_Air780EHV_13.soc`；编译使用已有 `tools/luatos-cli`，
输出位于临时目录，不覆盖 `build/script.bin`。不接收用户上传的固件或任意地址。
下载代理为固定 EC718 USB AgentBoot，准备时校验固定长度与 SHA-256，来源见
[资源说明](resources/webflash-source.md)。

通话、待发送短信、定位、设备网络测试或 USB 网卡操作未完成时，暂停请求返回 409。
暂停期间快照为 `bridge.connected=false`、`bridge.ready=false`、
`bridge.error="browser_flashing"`；WebSocket 与服务继续运行，设备命令被拒绝。
恢复桥接只恢复连接及状态/收件箱同步，不会重放短信、拨号或刷写命令。

刷写成功、失败或关闭浏览器都不会自动恢复桥接；同一后端进程运行期间保留暂停状态，
需手动调用恢复接口或通过刷写页面恢复。暂停状态不写入数据库，重启后端会恢复默认连接行为，
所以刷写过程中不要重启控制台服务。

自动测试使用模拟串口验证端口关闭后才确认释放、繁忙拒绝及 Origin 限制；
文件准备测试编译本机项目但不烧录设备。用户实机刷写结果与自动测试分开记录于主文档。

## macOS 桌面封装

`desktop_entry.py` 是 PyInstaller 打包入口，Electron 启动其 ARM64 可执行文件，
无需安装 Python。仅监听本机 9012，与开发环境的 9010/9011 分离。
入口设置 `AIR780_PROJECT_DIR` 读取应用资源目录中的前端、固件、Lua 和 CLI；
`AIR780_DATA_DIR` 指向用户应用支持目录，`AIR780_DESKTOP_ORIGIN` 仅允许指定的
本机 9xxx HTTP Origin。开发环境的默认路径和端口不变。

Electron 关闭子进程 stdin 时，入口请求 uvicorn 正常退出并执行清理；
桌面应用意外退出后 stdin EOF 同样触发清理。只结束应用自身启动的进程。
`AIR780_DESKTOP_INSTANCE` 是启动实例标识，由健康接口返回，供 Electron 确认
等待的服务属于本次启动；它不是访问凭据。
构建和运行说明见 [桌面应用文档](../desktop/README.md)。

## 钉钉 Stream 引用回复短信

`GET /api/dingtalk/replies` 读取连接状态、配置是否包含密钥、来源 ID 和最近回复请求；
`PUT /api/dingtalk/replies` 保存配置并重建接收连接，要求本机页面 Origin。
字段包含 `enabled`（接收连接）、`allow_sending`（允许实际短信回复）、`client_id`、
`client_secret`、`conversation_id`、`user_ids` 和 `clear_secret`。
新增 `push_enabled`（应用机器人收件推送）、`push_conversation_id`（留空使用 `conversation_id`）、
`robot_code`（可选，留空使用 Client ID）。连接、收件推送和 SMS 回复分别授权；新增推送默认关闭，
旧配置自动兼容；启用推送须能解析到具体群，两项群 ID 不能同时为空。仅连接时可发现群、用户 ID 和 RobotCode；API 的 `deliveries` 返回独立的 Stream 推送记录。
Webhook 与 Stream 推送独立处理新收件事件，历史同步不推送；两者同时开启分别推送。
应用机器人通过 `/v1.0/oauth2/accessToken` 取内存缓存令牌，以 `sampleText` 调用
`/v1.0/robot/groupMessages/send`，不依赖 Webhook 或临时会话地址。
SQLite `stream_sms_pushes` 保留去重与投递结果；关闭或修改配置会跳过尚未发送的队列项，
未知结果和重启不自动重发。两个模式中任一成功推送的原始收件都可以被引用回复。
凭据文件 `data/dingtalk/reply.json` 权限为 600，API 不返回密钥。

`conversation_id` 留空允许所有机器人所在群，`user_ids` 为空允许所有群成员；
非空时分别限制群和 `senderStaffId`。开启 `allow_sending` 前必须开启连接并配置应用凭据。
仅处理群内 @ 机器人的文本消息，通过原始
`text.repliedMsg.content.text` 中的短信编号查询本机已成功推送收件。
引用缺失时接受「回复 16位短信编号 内容」，不猜号码；必须是原 SIM。
复用 `Service.command` 的 SMS 校验、互斥与请求去重。
SQLite `dingtalk_replies` 持久化 Stream 请求，重启中的未完请求保持 unknown，不自动重发。
接收连接可重连，短信与会话回执均不自动重试；结果 submitted 只表示提交短信中心。

协议参考：[官方 Stream SDK](https://github.com/open-dingtalk/dingtalk-stream-sdk-python)。
本机无需入站公网端口，主动访问钉钉连接接口与安全 WebSocket；会话回执限定钉钉 HTTPS 地址。
