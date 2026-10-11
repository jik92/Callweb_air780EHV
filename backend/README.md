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
需要烧录时先停止服务，烧录后重新启动。

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
页面断线请求挂断；设备上行输入超时 5 秒及通话 90 秒上限为兜底。
