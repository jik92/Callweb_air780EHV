// EC718 protocol ported from wendal/luatos-cli (MIT).
// See backend/resources/luatos-cli-LICENSE and webflash-source.md.
export type SerialPort = {
  readable: ReadableStream<Uint8Array> | null
  writable: WritableStream<Uint8Array> | null
  open(options: { baudRate: number; bufferSize: number }): Promise<void>
  close(): Promise<void>
  setSignals(options: { dataTerminalReady: boolean }): Promise<void>
  getInfo(): { usbVendorId?: number; usbProductId?: number }
}
export type FlashImage = {
  name: string
  address: number
  identifier: number
  size: number
  sha256: string
  data: string
}
export type FlashPlan = {
  firmware: string
  chip: string
  mode: 'full' | 'script'
  baud: number
  agent: FlashImage
  entries: FlashImage[]
}
export const serialApi = () =>
  (
    navigator as Navigator & {
      serial?: {
        requestPort(options: {
          filters: { usbVendorId: number; usbProductId: number }[]
        }): Promise<SerialPort>
      }
    }
  ).serial
export const u32 = (n: number) =>
  new Uint8Array([n & 255, (n >>> 8) & 255, (n >>> 16) & 255, n >>> 24])
const number = (b: Uint8Array) =>
  new DataView(b.buffer, b.byteOffset, b.byteLength).getUint32(0, true)
export function crc32(data: Uint8Array) {
  let crc = 0xffffffff
  for (const b of data) {
    crc ^= b
    for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0)
  }
  return (crc ^ 0xffffffff) >>> 0
}
export function crc8(data: Uint8Array) {
  let crc = 0
  for (const b of data) {
    crc ^= b
    for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ (crc & 1 ? 0x8c : 0)
  }
  return crc
}
const join = (...parts: Uint8Array[]) => {
  const result = new Uint8Array(parts.reduce((n, p) => n + p.length, 0))
  let offset = 0
  for (const p of parts) {
    result.set(p, offset)
    offset += p.length
  }
  return result
}
export function commandFrame(cmd: number, data: Uint8Array, rom: boolean, lpc = false, index = 0) {
  const head = new Uint8Array([
    cmd,
    index,
    lpc ? 0x4c : 0xcd,
    lpc ? 0xb3 : 0x32,
    ...u32(data.length),
  ])
  const plain = join(head, data)
  if (rom) return cmd === 0x32 ? join(plain, u32(plain.reduce((a, b) => a + b, 0))) : plain
  const crc = crc32(plain)
  if (data.length) head[7] = crc8(head.subarray(4, 7))
  return join(head, data, u32(crc))
}
const decode = (image: FlashImage) => Uint8Array.from(atob(image.data), (c) => c.charCodeAt(0))
const digest = async (data: Uint8Array) =>
  new Uint8Array(await crypto.subtle.digest('SHA-256', new Uint8Array(data)))
export async function verifyPlan(plan: FlashPlan) {
  if (!plan.chip.startsWith('EC718HM') || !plan.entries.length)
    throw new Error('固件芯片或分区不匹配')
  for (const image of [plan.agent, ...plan.entries]) {
    const data = decode(image)
    const hash = Array.from(await digest(data), (b) => b.toString(16).padStart(2, '0')).join('')
    if (data.length !== image.size || hash !== image.sha256)
      throw new Error(`${image.name} 数据校验失败`)
  }
}
export async function imageHeader(
  data: Uint8Array,
  id: number,
  address: number,
  baud = 0,
  pullup = 0,
) {
  const head = new Uint8Array(272)
  const view = new DataView(head.buffer)
  for (const [offset, value] of [
    [0, 0x10000001],
    [4, 0x54494d48],
    [8, 0x20180507],
    [16, 1],
    [24, pullup],
    [64, id],
    [68, address],
    [72, 0x04010000],
    [76, data.length],
  ])
    view.setUint32(offset, value, true)
  head[20] = 0xee
  view.setUint16(22, baud ? (baud / 100 + 0x8000) & 0xffff : 0, true)
  head.set(await digest(data), 96)
  head.set(await digest(head), 32)
  return head
}
// DOWNLOAD_DATA acknowledgements may report the next expected block index.
// The EHV ROM trace confirms request index 0 -> successful ACK index 1.
export function responseIndexMatches(cmd: number, sent: number, received: number) {
  return received === sent || (cmd === 0x32 && received === ((sent + 1) & 255))
}
class SerialReadTimeout extends Error {}
const hex = (data: number[] | Uint8Array) =>
  Array.from(data, (b) => b.toString(16).padStart(2, '0')).join(' ')
export class WebFlasher {
  private reader: ReadableStreamDefaultReader<Uint8Array> | null = null
  private writer: WritableStreamDefaultWriter<Uint8Array> | null = null
  private pump: Promise<void> | null = null
  private buffer: number[] = []
  private failure: Error | null = null
  private wake: (() => void) | null = null
  private opened = false
  private context = '串口读取'
  private port: SerialPort
  private signal: AbortSignal
  private report: (progress: number, text: string) => void
  constructor(
    port: SerialPort,
    signal: AbortSignal,
    report: (progress: number, text: string) => void,
  ) {
    this.port = port
    this.signal = signal
    this.report = report
  }
  private check() {
    if (this.signal.aborted) throw new Error('刷写已停止；结果未知，请进入 BOOT 模式重新刷写')
    if (this.failure) throw this.failure
  }
  private async read(size: number, timeout = 5000) {
    const deadline = Date.now() + timeout
    while (this.buffer.length < size) {
      this.check()
      if (Date.now() >= deadline)
        throw new SerialReadTimeout(
          `${this.context}超时：需要 ${size} 字节，已收到 ${this.buffer.length} 字节${this.buffer.length ? `（${hex(this.buffer.slice(0, 32))}）` : ''}；结果未知，未自动重试写入`,
        )
      await new Promise<void>((resolve) => {
        const timer = setTimeout(
          () => {
            this.wake = null
            resolve()
          },
          Math.min(100, deadline - Date.now()),
        )
        this.wake = () => {
          clearTimeout(timer)
          this.wake = null
          resolve()
        }
      })
    }
    this.check()
    return new Uint8Array(this.buffer.splice(0, size))
  }
  private async write(data: Uint8Array) {
    this.check()
    // Match the native macOS transport's 64-byte pacing.
    for (let offset = 0; offset < data.length; offset += 64) {
      this.check()
      await this.writer!.write(data.subarray(offset, offset + 64))
      await new Promise((resolve) => setTimeout(resolve, 1))
    }
  }
  private async sync(value: number, rom = false) {
    const bytes = u32(value)
    const label = rom ? 'DLBOOT' : value === 0x2b02d3cd ? 'LPC' : 'AGBOOT'
    this.context = `${label} 握手`
    for (let attempt = 0; attempt < 40; attempt++) {
      // Preserve the working native tool's separate four-byte writes and pacing.
      await this.write(bytes)
      await new Promise((resolve) => setTimeout(resolve, 2))
      await this.write(bytes)
      await new Promise((resolve) => setTimeout(resolve, 2))
      try {
        const reply = await this.read(4, 800)
        if (rom) {
          // ROM may echo only four bytes; its optional status byte must be drained
          // before sending GET_VERSION, including when delivered in a later packet.
          try {
            const extra = await this.read(1, 800)
            if (extra[0] !== 0) throw new Error(`DLBOOT 握手状态异常：${hex(extra)}`)
          } catch (error) {
            if (!(error instanceof SerialReadTimeout)) throw error
          }
        }
        if (number(reply) === value) return
        throw new Error(`${label} 握手响应不匹配：${hex(reply)}`)
      } catch (error) {
        this.check()
        if (!(error instanceof SerialReadTimeout) || attempt === 39) throw error
      }
    }
    throw new Error(`${label} 下载握手失败，请重新进入 BOOT 模式并选择下载口`)
  }
  private async command(cmd: number, data = new Uint8Array(), rom = false, lpc = false, index = 0) {
    const names: Record<number, string> = {
      0x20: 'GET_VERSION',
      0x21: 'SEL_IMAGE',
      0x22: 'VERIFY_IMAGE',
      0x31: 'DATA_HEAD',
      0x32: 'DOWNLOAD_DATA',
      0x3a: 'DONE',
      0x42: 'BURN_ONE',
      0x44: 'BURN_STATUS',
      0xaa: 'RESET',
    }
    const label = `${rom ? 'DLBOOT' : lpc ? 'LPC' : 'AGBOOT'} ${names[cmd] ?? cmd.toString(16)} (序号 ${index})`
    this.context = `${label} 响应头`
    await this.write(commandFrame(cmd, data, rom, lpc, index))
    const head = await this.read(6)
    if (
      head[0] !== cmd ||
      !responseIndexMatches(cmd, index, head[1]) ||
      head[2] !== (lpc ? 0x4c : 0xcd) ||
      head[3] !== (lpc ? 0xb3 : 0x32)
    )
      throw new Error(`${label} 响应不匹配：${hex(head)}`)
    this.context = `${label} 响应数据（响应头 ${hex(head)}）`
    const body = await this.read(head[5])
    if (!rom) {
      this.context = `${label} 响应 CRC`
      const crc = number(await this.read(4))
      if (crc32(join(head, body)) !== crc) throw new Error('串口响应 CRC 校验失败')
    }
    if (head[4] !== 0) throw new Error(`模块拒绝命令 0x${cmd.toString(16)}，状态 ${head[4]}`)
    return body
  }
  private async base(rom: boolean, id: number) {
    await this.command(0x20, undefined, rom)
    const selected = await this.command(0x21, undefined, rom)
    if (selected.length < 4 || number(selected) !== id) throw new Error('模块请求的镜像类型不匹配')
    await this.command(0x22, undefined, rom)
  }
  private async transfer(data: Uint8Array, rom: boolean) {
    let offset = 0,
      index = 0
    while (offset < data.length) {
      const response = await this.command(0x31, u32(data.length - offset), rom)
      if (response.length < 4) throw new Error('模块未返回传输块大小')
      const size = Math.min(number(response), data.length - offset)
      if (!size || size > 0x10000) throw new Error('模块返回的传输块大小无效')
      await this.command(0x32, data.slice(offset, offset + size), rom, false, index++ & 255)
      offset += size
    }
    await this.command(0x3a, undefined, rom)
  }
  async close() {
    if (this.reader) {
      await this.reader.cancel().catch(() => {})
      await this.pump
      this.reader.releaseLock()
      this.reader = null
    }
    this.writer?.releaseLock()
    this.writer = null
    if (this.opened) {
      await this.port.close()
      this.opened = false
    }
  }
  async run(plan: FlashPlan) {
    try {
      await verifyPlan(plan)
      await this.port.open({ baudRate: 921600, bufferSize: 65536 })
      this.opened = true
      await this.port.setSignals({ dataTerminalReady: true })
      if (!this.port.readable || !this.port.writable) throw new Error('串口不可读写')
      this.reader = this.port.readable.getReader()
      this.writer = this.port.writable.getWriter()
      this.pump = (async () => {
        try {
          while (true) {
            const { value, done } = await this.reader!.read()
            if (done) {
              this.failure = new Error('串口断开，刷写结果未知')
              break
            }
            this.buffer.push(...value)
            if (this.buffer.length > 1024 * 1024) throw new Error('串口响应缓冲区溢出')
            this.wake?.()
          }
        } catch {
          this.failure = new Error('串口读取失败，刷写结果未知')
        } finally {
          this.wake?.()
        }
      })()
      this.report(2, '等待 BOOT 下载握手')
      await this.sync(0x2b02d300, true)
      this.report(5, 'BOOT 握手已确认，读取下载器版本与镜像类型')
      const agent = decode(plan.agent)
      await this.base(true, 0x54494d48)
      this.report(6, '下载器版本与镜像类型已确认，发送 AgentBoot 镜像头')
      await this.transfer(await imageHeader(agent, 0x4f424d49, 0, plan.baud, 1), true)
      this.report(7, 'AgentBoot 镜像头已确认，重新握手')
      await this.sync(0x2b02d300, true)
      await this.base(true, 0x4f424d49)
      this.report(8, `传输 AgentBoot（${agent.length} 字节）`)
      await this.transfer(agent, true)
      this.report(15, 'AgentBoot 已加载')
      const total = plan.entries.reduce((n, image) => n + image.size, 0)
      let written = 0
      for (const image of plan.entries) {
        this.report(15 + (written / total) * 80, `开始写入 ${image.name}`)
        const data = decode(image)
        await this.sync(0x2b02d3cd)
        await this.command(0x42, u32(image.identifier), false, true)
        await this.sync(0x2b02d3aa)
        await this.sync(0x2b02d3aa)
        await this.base(false, 0x54494d48)
        await this.transfer(await imageHeader(data, image.identifier, image.address), false)
        for (let offset = 0; offset < data.length; offset += 0x10000) {
          await this.sync(0x2b02d3aa)
          const block = data.slice(offset, offset + 0x10000)
          await this.transfer(block, false)
          written += block.length
          this.report(
            15 + (written / total) * 80,
            `${image.name}：${Math.min(offset + block.length, data.length)} / ${data.length} 字节`,
          )
        }
        const status = await this.command(0x44, undefined, false, true)
        if (status.length !== 4 || number(status) !== 0)
          throw new Error(`${image.name} 烧录校验失败`)
      }
      this.report(96, '所有分区已校验，正在请求重启')
      await this.sync(0x2b02d3cd)
      const ack = await this.command(0xaa, undefined, false, true)
      if (new TextDecoder().decode(ack) !== 'ZzZzZzZz')
        throw new Error('未收到重启确认，请检查设备状态')
      this.report(100, '刷写完成，模块已确认重启；正常上电后恢复桥接')
    } finally {
      await this.close()
    }
  }
}
