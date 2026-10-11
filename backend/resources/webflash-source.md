# Web Serial 刷写来源

协议移植自 https://github.com/wendal/luatos-cli ，commit
`1bf95b4d38da3d97893a9586083d55b92b9233cc` 的
`crates/luatos-flash/src/ec718.rs`，MIT 许可见 luatos-cli-LICENSE。

`ec718m_usb.bin` 原样取自该仓库
`refs/origin_tools/ec_download/agentboot/ec718m_usb.bin`，作为 EC718 USB
下载代理，运行于模块 RAM。未修改供应商二进制。

Chrome Web Serial 文档：https://developer.chrome.com/docs/capabilities/serial

AgentBoot 实际二进制从同一 commit 的 Git LFS 媒体取回（不是 LFS 指针）：
大小 40696 字节，SHA-256
`92a596c9a2f339e1427ec6f688115846983e99f8f7a2f835457f762fa6077a51`。
文件准备时强制校验这一固定摘要与长度。
