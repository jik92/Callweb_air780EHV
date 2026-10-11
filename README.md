# Air780EHV macOS LuatOS 开发环境

此项目参考 `../uart_sms_forwarder-main/main.lua`，使用合宙官方 LuatOS Lua API。
设备程序查询 SIM 和网络信息，并支持按 USB 命令发送短信，通过 USB VUART 返回 JSON。

## 本地环境

- Apple Silicon macOS；`tools/luatos-cli` 1.11.0 原生 ARM64 工具。
- 官方源码及示例：`sdk/LuatOS/`（稀疏检出，版本见 `environment.json`）。
- 官方固件：`firmware/LuatOS-SoC_V2052_Air780EHV_13.soc`。
- Python 虚拟环境：`.venv/`，串口依赖 `pyserial==3.5`。
- VS Code 已安装 Lua 扩展，项目设置位于 `.vscode/`。
- 查询程序：`lua/main.lua`；上位机查询：`scripts/device.py`。

## 编译与检查

```sh
cd /path/to/Callweb_air780EHV
./scripts/build.sh
.venv/bin/python scripts/device.py ports
```

构建生成 `build/script.bin`，使用 LuatOS 的 32 位 Lua 字节码。
语法、依赖和脚本分区容量由 CLI 检查。

## 烧录

首次使用 LuatOS 或更换底层固件：

```sh
./scripts/flash.sh
```

后续仅修改 Lua 脚本：

```sh
./scripts/flash-script.sh
```

macOS 上 CLI 的 USB 接口自动选择可能选错端口。如果自动进入下载模式超时，
按住“下载/BOOT”键，同时复位或重新插入 USB，再松开下载键。
下载模式通常出现 `/dev/cu.usbmodem0000000000011`（VID/PID `17D1:0001`）；
实际路径以串口列表为准，可将端口传给烧录脚本：

```sh
./scripts/flash.sh /dev/cu.usbmodem0000000000011
```

烧录期间保持 USB 连接，不按按钮。更换底层固件会替换设备当前应用。
已生成包含本项目脚本的 `build/air780ehv-sim-query.soc`，可用于 LuaTools 全量烧录。

## 读取手机号

烧录并启动后：

```sh
.venv/bin/python scripts/device.py status
```

输出 `mobile.number`，以及 SIM 就绪状态、ICCID、IMSI、注册状态和信号。
查询程序 `001.999.001` 还返回当前 SIM 卡槽、RSSI、RSRP、RSRQ、SNR，
以及 `mobile.scell()` 返回的服务小区（MCC/MNC、Band、CID、TAC、PCI、EARFCN）。
这些小区和信号数据反映当前连接的网络，不属于 SIM 卡的固定资料。
退出码：`0` 已返回号码；`2` 收到状态但号码为空；`1` 没有设备状态响应。
串口查询兼容参考项目的 `get_status` 协议。

本机实测 USB VUART 为 `/dev/cu.usbmodem0000000000017`，可直接指定：

```sh
.venv/bin/python scripts/device.py status --port /dev/cu.usbmodem0000000000017
```

2026-10-11 实机验证：V2052 固件和查询脚本烧录成功；返回本项目名称与版本，
SIM 就绪、网络状态为 1（已注册）、CSQ 为 31，ICCID 和 IMSI 可读取，手机号为空。
完整结果在 `logs/luatos-status.json`，成功烧录日志在 `logs/flash-retry.log`。
这里确认的是接口未返回号码，不能仅据此认定空值的具体原因。
扩展查询实测：SIM0；服务网络 MCC/MNC 为 460/11，Band 8；
RSRP -67 dBm，RSRQ -8 dB，SNR 15 dB。这些值会随时间和设备位置变化。

`mobile.number(0)` 只能读取 SIM 中预先存储的号码，必须等 SIM 准备就绪。
空值并不表示 SIM 不正常，不能由 ICCID、IMSI 推算出手机号。
若没有存储号码，需要通过运营商账户或其他已授权方式确认后配置。

本机实测 LuatOS 二进制日志口为 `/dev/cu.usbmodem0000000000013`：

```sh
tools/luatos-cli log view-binary --port /dev/cu.usbmodem0000000000013 --baud 921600 --probe
```

原始 AT 固件诊断命令（仅适用于运行 AT 固件时）：

```sh
.venv/bin/python scripts/device.py at --port /dev/cu.usbmodem0000000000013
```

日志保存在 `logs/`；设备 SIM 信息请留在本机。

## 发送短信

程序 `001.999.002` 在收到明确的 `send_sms` 命令后调用官方
`sms.sendLong(...).wait()`；上电不会自动发送。

```sh
.venv/bin/python scripts/send_sms.py \
  --port /dev/cu.usbmodem0000000000017 \
  --to <收件号码> --content '<短信内容>'
```

该命令会实际发送短信。上位机发送一次请求，不自动重试；结果按 request_id 匹配。
`sent_to_smsc` 表示模块报告提交短信中心成功，不是收件手机的送达确认。
超时或串口异常时结果为 unknown，需检查日志，避免重复发送。
每次请求及其结果保存在 `logs/sms-<request_id>.json`。

2026-10-11 实测发送一条中文测试短信，模块返回 `success=true`。

## 读取回复短信

程序 `001.999.003` 使用官方 `sms.setNewSmsCb` 接收短信，将最近 20 条
保存在设备文件 `/sms_inbox.json`，并通过 USB 推送 `incoming_sms`。
USB 未连接时也会保存；重新启动会加载保存的记录。
无法补读在接收回调部署前未被应用保存的短信。

```sh
# 查看设备保存的收件箱（不会发送短信）
.venv/bin/python scripts/read_sms.py
# 查看收件箱并监听新短信 60 秒
.venv/bin/python scripts/read_sms.py --listen 60
```

本机读取记录追加到 `logs/sms-inbox.jsonl`，包括发件人、内容和时间信息。
同一 USB VUART 同时只运行一个查询/发送/监听程序，避免多个进程抢读数据。
2026-10-11 已实测收到手机回复，设备报告 `saved=true`，随后通过 `get_inbox`
再次读到相同短信，接收与收件箱查询均正常。

## 官方资料与工具来源

- [官方 LuatOS 源码](https://github.com/openLuat/LuatOS)
- [Air780EHV 固件](https://docs.openluat.com/air780ehv/luatos/firmware/version/)
- [mobile.number API](https://docs.openluat.com/osapi/core/mobile/#mobilenumberid)
- [官方 README 指向的跨平台 CLI](https://gitee.com/openLuat/luatos-cli)
- [本机 CLI 二进制来源](https://github.com/wendal/luatos-cli/releases/tag/v1.11.0)
- [CLI 对 Air780EHV 的说明](https://github.com/wendal/luatos-cli/blob/main/docs/models/air9010-ec7xx.md)

## 通话中播报文字

程序 `001.999.007` 使用官方 13 号固件的新音频框架，`cc.dial` 拨号，
接通且收到 `AUDIO_START` 后通过 `cc.extern_source` 向通话对端播放 TTS。
上电不会拨号，不自动接听来电。播完等待 2 秒挂断；45 秒未接通挂断，
单次通话上限 90 秒。现有短信收发功能继续保留。

```sh
.venv/bin/python scripts/call_tts.py \
  --port /dev/cu.usbmodem0000000000017 \
  --to <接听号码> --content '<播报文字>'
```

此命令会实际拨打一通电话，不自动重试。结果保存在 `logs/call-<request_id>.json`。
`connected` 表示接通事件，`played` 表示模块收到外部音频解码完成事件，
无法仅凭模块日志确认对方实际听到的效果。

音频库 `lua/exaudio.lua`、`lua/es8311.lua` 复制自本机官方 SDK，业务代码在
`lua/call_player.lua`。更换底层固件前已将收件箱备份到
`logs/inbox-before-call-upgrade.jsonl`。
参考 [官方通话音频示例](https://docs.openluat.com/air780ehv/luatos/app/volte/audio_play_to_call/)。

### 接收对端语音

加上 `--listen-seconds 30`，播报后继续保持通话 30 秒，采集下行音频。
使用 `cc.record` 双缓冲，在设备 RAM 中缓存最多 2049010 字节 PCM，
通话结束后分块导出到本机 WAV（单声道、16 位，8K/16K 由通话质量决定）。
此实现不转写文字。文件保存在 `logs/call-<request_id>.wav`。

2026-10-11：首通 TTS 通话接通、播报完成、正常挂断，用户确认成功。
第二通实时音频传输收到 2.82 秒下行录音，但设备重启、录音不完整。
随后 `001.999.007` 改为通话期间缓存、结束后导出，已编译和部署；
新缓存方案随后实测接通和播报完成，导出 9.36 秒、16KHz 下行 WAV，
74 个导出数据块序号连续，无缓冲溢出，设备未重启，结束后查询正常。
录音保存为 `logs/call-<request_id>.wav`。
底层固件升级后的收件箱已从升级前备份恢复并读回确认。

## 本机 WebSocket 后台

现已提供 FastAPI 串口桥接服务和 React 双栏后台，使用 uv、Ruff、TanStack Query / Router、Ant Design、Tailwind CSS 4。
直接启动开发环境：

```sh
./scripts/dev-console.sh
```

访问 **http://127.0.0.1:9011**。FastAPI 在 `http://127.0.0.1:9010`，WebSocket 路径 `/ws`。
启动脚本后台运行两个进程；`./scripts/dev-console.sh stop` 停止，
`./scripts/dev-console.sh restart` 重启，`./scripts/dev-console.sh status` 查看。
无登录、无用户管理，服务监听本机。设备状态和短信事件实时推送，页面支持
SIM 信息读取、刷新、收件箱/发件记录筛选、搜索、发送短信、回复号码自动填入。

短信控制台支持 `001.999.007` 及更新的设备脚本；实时拨号需要 `001.999.008`，来电通知与接听需要 `001.999.009`。
服务独占 `/dev/cu.usbmodem0000000000017`；停止服务后才运行其他串口工具或烧录。
可用 `AIR780_SERIAL_PORT` 指定端口。服务与浏览器自动重连，发送请求不自动重试。
短信记录保存在 `data/console.sqlite3`；页面最多显示最近 500 条，设备最多补读最近 20 条收件。

如只需一个端口提供完整后台，先构建，再启动 FastAPI：

```sh
npm run build --prefix frontend
uv run --directory backend uvicorn app.main:app --host 127.0.0.1 --port 9010
```

此时访问 http://127.0.0.1:9010。检查全部代码：`./scripts/check-console.sh`。
详细说明见 [后端](backend/README.md) 与 [前端](frontend/README.md)。

2026-10-11 实机联调：从 React 页面通过 WS 发送测试短信到指定号码，模块返回提交成功；
用户手机回复测试短信，页面无需刷新即显示，SIM 信息读取正常。

服务重启后，已实测 WS 自动恢复、3 条短信记录保留，相同发送请求 ID 返回去重确认。
测试结果：Ruff、前端格式检查、TypeScript/Vite 构建通过，10 个后端测试通过。

项目开发约定见 [AGENTS.md](AGENTS.md)。前端统一使用 Tailwind，禁止手写原生 CSS 和内联样式，检查脚本已包含样式约束。

## 网页实时通话验证

实时通话页面保存每次拨号的号码、时间与结果，接通后自动保存本机双向音频，
结束后支持录音回放和 WAV 下载；刷新或重启后台后仍可查询。
等待接通时播放本地“滴滴”提示音，接通或结束时停止，不向手机端发送。
未接通的记录没有录音，本功能部署前的历史通话无法补录。

当前新增 `001.999.008` 设备脚本与 WebSocket 双向 PCM 桥接。
主页面“实时通话”支持输入号码拨号、挂断、麦克风静音以及电脑耳机播放对方语音。
电话键盘支持号码输入、删除、清空、国际区号以及填入上次号码；接通后可发送
`0–9`、`*`、`#` 的带内 DTMF 按键音，静音时仍可使用。语音菜单识别需实机验证。
使用浏览器麦克风，需要允许麦克风权限；建议戴耳机。单次通话最长 90 秒。
控制台继续使用 9010 / 9011，音频通道为 `/ws/call`，状态可查询 `/api/call`。
当前为 WebSocket 验证方案，后续可把浏览器到后台的媒体通道替换成 WebRTC。
详细协议与验证限制见前后端 README。

2026-10-11 实机验证：设备脚本已更新为 `001.999.008`，网页测试电话接通，
16kHz 双向 PCM 链路累计接收 380160 字节，设备实际写入 378880 字节，
未报告丢帧；通话结束后设备仍在线，原有 5 条短信保留。
这些计数确认音频链路工作，双方听感、延迟和回声仍需人工确认。
本机验证记录为 `logs/live-call-validation.json`。

### 来电通知与网页接听

设备脚本 `001.999.009` 将来电主动推送到已打开的控制台。任何菜单下都会弹出来电号码和接听/拒接按钮；接听前先获取麦克风权限，成功后进入实时通话页面。设备不上电自动拨号，也不自动接听。同一通来电只允许一个页面接听，其他页面可查看状态。

使用电脑麦克风和耳机进行双向通话，支持静音、通话按键音及双向录音。实时通话不再有验证版 90 秒强制结束限制；音频中断、接听页面断开仍会请求挂断，不会自动重拨。TTS 播报任务保留原有时限。页面需保持打开才能显示通知。

### Agent电话与 AGENT设置

实时通话菜单下增加「通话控制」与「AGENT设置」。来电弹窗可选择普通「接听」、
「Agent接听」或「拒接」；普通通话继续使用电脑麦克风，Agent接听由后台直接桥接
模块音频和 Realtime WebSocket，无需电脑麦克风。不会自动接听或拨号。

在 AGENT设置中选择 AiHubMix 或 OpenAI 官方、配置 API Key、模型（默认
`gpt-realtime-2.1`）、声音、开场白和场景指令，启用并保存。Key 仅保存于忽略目录
`data/agent/settings.json`（文件权限 600），API 不回传 Key。也可分别使用后台环境变量
`AIHUBMIX_API_KEY` / `OPENAI_API_KEY`；切换提供方时必须使用对应 Key。

场景指令保存为 `data/agent/AGENT.md`。每通 Agent电话接听前读取一次，修改设置或
直接编辑该文件从下一通生效。默认提供通用电话助手模板，需要填写实际业务背景与标准回复。
「验证已保存的连接」仅检查 Realtime 会话配置，不发送电话音频、不接听或拨号。
Agent接听先完成 Realtime配置验证，失败时保留来电供普通接听或拒接。

后台使用流式重采样连接设备 8/16 kHz 与 Realtime 24 kHz PCM，按 20 ms 节奏向电话
发送语音或静音。支持来电人打断，清理待播队列并截断模型会话中的未播放回复；
已经写入模块的少量音频无法撤回。双向录音继续保存于本机。API中断、队列拥塞或接听页面
断开会请求挂断，不自动重试。通话音频与场景指令发送至所选提供方，会产生 API 费用。
Agent仅进行语音回复，不授予工具或业务执行权限。

接口与协议参考：[AiHubMix Realtime示例](https://aihubmix.com/model/gpt-realtime-2.1)、
[OpenAI Realtime WebSocket](https://developers.openai.com/api/docs/guides/voice-websockets?voice-api=realtime)。

### 短信钉钉推送

在「短信管理 → 钉钉推送」配置自定义机器人 Webhook 和 SEC 加签密钥，保存后可开关推送、发送固定测试消息以及查看最近 20 条推送状态。测试使用已保存配置，可以在自动推送关闭时执行。

启用后仅推送新收到的短信，包含发送号码、接收时间和短信内容；历史收件箱同步和发件短信不推送。后台异步限速处理，不阻塞短信与通话。SQLite 保存推送去重状态，相同短信不重复推送；失败、超时及重启后未知结果不自动重试。

凭据仅保存在忽略目录 `data/dingtalk/settings.json`（目录 700、文件 600），页面仅显示是否已配置，不回传密钥。Webhook 固定允许钉钉官方 HTTPS 机器人地址；写入和测试接口检查本机页面 Origin。无需修改或烧录设备脚本。

### 设备网络测试

「网络测试」菜单通过 USB 下发一次设备端 HEAD 请求，使用 SIM 卡的 4G 网络访问 HTTP/HTTPS 地址，默认 `https://www.baidu.com`。页面显示响应状态、耗时和部分响应头；HTTP 4xx/5xx 仍表示网络已连通，部分网站不接受 HEAD。测试不下载网页正文，也不会在启动或重连时自动执行。

需设备脚本 `001.999.011` 或更新版本（状态包含 `network_test_supported`）。通话中禁止发起测试；设备等待数据网络最多 5 秒，HTTP 请求最多 15 秒，页面等待最多 25 秒。连接中断或超时不会自动重试。此功能保持本机 API 直接读取 USB 的单节点架构。
