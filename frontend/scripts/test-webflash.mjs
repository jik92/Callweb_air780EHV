import assert from 'node:assert/strict'
import {
  commandFrame,
  responseIndexMatches,
  crc8,
  crc32,
  imageHeader,
  u32,
  verifyPlan,
  WebFlasher,
} from '../src/webFlash.ts'
assert.equal(crc8(new TextEncoder().encode('123456789')), 0xa1)
assert.equal(crc32(new TextEncoder().encode('123456789')), 0xcbf43926)
assert.deepEqual(
  [...commandFrame(0x32, new Uint8Array([1, 2, 3, 4]), true)],
  [0x32, 0, 0xcd, 0x32, 4, 0, 0, 0, 1, 2, 3, 4, 0x3f, 1, 0, 0],
)
const image = async (name, address, identifier, bytes) => ({
  name,
  address,
  identifier,
  size: bytes.length,
  data: Buffer.from(bytes).toString('base64'),
  sha256: Buffer.from(await crypto.subtle.digest('SHA-256', bytes)).toString('hex'),
})
const plan = {
  chip: 'EC718HM_PRD',
  firmware: 'mock.soc',
  mode: 'script',
  baud: 1152000,
  agent: await image('agent', 0, 0x4f424d49, new Uint8Array([1, 2, 3])),
  entries: [await image('script', 0xd6e000, 0x464c5849, new Uint8Array(70000).fill(42))],
}
await verifyPlan(plan)
await assert.rejects(verifyPlan({ ...plan, agent: { ...plan.agent, sha256: 'bad' } }), /校验失败/)
const header = await imageHeader(new Uint8Array([1, 2, 3]), 0x4f424d49, 0, 1152000, 1)
assert.equal(header.length, 272)
assert.equal(new DataView(header.buffer).getUint32(72, true), 0x04010000)
const expected = header.slice(32, 64)
header.fill(0, 32, 64)
assert.deepEqual(expected, new Uint8Array(await crypto.subtle.digest('SHA-256', header)))
function mockPort({
  corrupt = false,
  disconnect = false,
  romStatus = true,
  delayedStatus = false,
  nextIndex = true,
  wrongIndex = false,
} = {}) {
  let incoming,
    wire = [],
    rom = true,
    selected = 0x54494d48,
    transfers = 0,
    closed = 0
  const port = {
    readable: new ReadableStream({
      start(c) {
        incoming = c
      },
    }),
    writable: new WritableStream({
      write(chunk) {
        if (chunk[1] === 0xd3 && chunk[2] === 2 && chunk[3] === 0x2b)
          assert.equal(chunk.length, 4, 'handshake words must be sent separately')
        wire.push(...chunk)
        while (wire.length >= 8) {
          if (wire[1] === 0xd3 && wire[2] === 2 && wire[3] === 0x2b) {
            const sync = wire.splice(0, 8).slice(0, 4)
            rom = sync[0] === 0
            incoming.enqueue(new Uint8Array(sync))
            if (rom && romStatus) {
              if (delayedStatus) setTimeout(() => incoming.enqueue(new Uint8Array([0])), 10)
              else incoming.enqueue(new Uint8Array([0]))
            }
            continue
          }
          const cmd = wire[0],
            index = wire[1],
            lpc = wire[2] === 0x4c
          const len = (wire[4] | (wire[5] << 8) | (wire[6] << 16)) >>> 0
          const size = 8 + len + (!rom || cmd === 0x32 ? 4 : 0)
          if (wire.length < size) break
          const packet = wire.splice(0, size)
          assert.deepEqual(
            new Uint8Array(packet),
            commandFrame(cmd, new Uint8Array(packet.slice(8, 8 + len)), rom, lpc, index),
          )
          let body = new Uint8Array()
          if (cmd === 0x21) body = u32(selected)
          if (cmd === 0x31) body = u32(4096)
          if (cmd === 0x32) {
            transfers++
            if (len === 272)
              selected = new DataView(new Uint8Array(packet).buffer).getUint32(8 + 64, true)
          }
          if (cmd === 0x42) selected = 0x54494d48
          if (cmd === 0x44) body = u32(0)
          if (cmd === 0xaa) body = new TextEncoder().encode('ZzZzZzZz')
          if (disconnect && transfers === 3) {
            incoming.close()
            return
          }
          const head = new Uint8Array([
            cmd,
            cmd === 0x32 ? (index + (wrongIndex ? 3 : nextIndex ? 1 : 0)) & 255 : index,
            lpc ? 0x4c : 0xcd,
            lpc ? 0xb3 : 0x32,
            0,
            body.length,
          ])
          const plain = new Uint8Array([...head, ...body])
          const response = new Uint8Array([
            ...plain,
            ...(rom ? [] : u32(crc32(plain) ^ (corrupt ? 1 : 0))),
          ])
          // Fragment every reply to exercise buffered reads.
          incoming.enqueue(response.slice(0, 3))
          incoming.enqueue(response.slice(3))
        }
      },
    }),
    async open() {},
    async setSignals() {},
    async close() {
      closed++
    },
    getInfo() {
      return { usbVendorId: 0x17d1, usbProductId: 1 }
    },
  }
  return {
    port,
    get closed() {
      return closed
    },
    get transfers() {
      return transfers
    },
  }
}
const device = mockPort()
const progress = []
await new WebFlasher(device.port, new AbortController().signal, (p) => progress.push(p)).run(plan)
assert.equal(progress.at(-1), 100)
assert.equal(device.closed, 1)
assert.ok(device.transfers > 10)
const damaged = mockPort({ corrupt: true })
await assert.rejects(
  new WebFlasher(damaged.port, new AbortController().signal, () => {}).run(plan),
  /CRC/,
)
assert.equal(damaged.closed, 1)
const unplugged = mockPort({ disconnect: true })
await assert.rejects(
  new WebFlasher(unplugged.port, new AbortController().signal, () => {}).run(plan),
  /断开/,
)
assert.equal(unplugged.closed, 1)
const canceled = mockPort()
const stop = new AbortController()
stop.abort()
await assert.rejects(new WebFlasher(canceled.port, stop.signal, () => {}).run(plan), /停止/)
assert.equal(canceled.transfers, 0)
assert.equal(canceled.closed, 1)
console.log('Web Serial mock: transfer, CRC rejection, disconnect and cancellation passed')

for (const options of [{ romStatus: false }, { delayedStatus: true }]) {
  const variant = mockPort(options)
  const events = []
  await new WebFlasher(variant.port, new AbortController().signal, (p, text) =>
    events.push(text),
  ).run(plan)
  assert.equal(variant.closed, 1)
  assert.ok(events.some((text) => text.includes('BOOT 握手已确认')))
}
// Known wire vector from Cmd::new(CMD_GET_VERSION).pack(), not a mock-generated value.
assert.deepEqual([...commandFrame(0x20, new Uint8Array(), true)], [0x20, 0, 0xcd, 0x32, 0, 0, 0, 0])
const silent = {
  readable: new ReadableStream(),
  writable: new WritableStream(),
  async open() {},
  async close() {},
  async setSignals() {},
  getInfo() {
    return {}
  },
}
const flasher = new WebFlasher(silent, new AbortController().signal, () => {})
flasher.context = 'DLBOOT GET_VERSION 响应头'
await assert.rejects(flasher.read(6, 1), /GET_VERSION 响应头超时：需要 6 字节，已收到 0 字节/)
console.log(
  'Handshake regressions passed: separate writes, optional and delayed ROM status, command timeout context',
)

// Recorded EHV ACK: request DOWNLOAD_DATA index 0 -> 32 01 cd 32 00 00.
const recordedAck = new Uint8Array([0x32, 1, 0xcd, 0x32, 0, 0])
assert.ok(responseIndexMatches(recordedAck[0], 0, recordedAck[1]))
assert.ok(responseIndexMatches(0x32, 255, 0))
assert.ok(responseIndexMatches(0x32, 7, 7))
assert.equal(responseIndexMatches(0x32, 0, 3), false)
assert.equal(responseIndexMatches(0x31, 0, 1), false)
const echo = mockPort({ nextIndex: false })
await new WebFlasher(echo.port, new AbortController().signal, () => {}).run(plan)
const wrong = mockPort({ wrongIndex: true })
await assert.rejects(
  new WebFlasher(wrong.port, new AbortController().signal, () => {}).run(plan),
  /DOWNLOAD_DATA.*响应不匹配/,
)
assert.equal(wrong.closed, 1)
console.log(
  'EHV recorded ACK regression passed: next block index, echoed index, wraparound and invalid index rejection',
)
