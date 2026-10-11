import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const code = readFileSync(new URL('../src/pcm-worklet.js', import.meta.url), 'utf8')
function capture(rate, digit, muted = true, seconds = 1) {
  let Processor
  const packets = []
  vm.runInNewContext(code, {
    AudioWorkletProcessor: class {
      constructor() {
        this.port = { postMessage: (packet) => packets.push(packet) }
      }
    },
    sampleRate: 48000,
    registerProcessor: (_name, value) => {
      Processor = value
    },
  })
  const node = new Processor()
  node.port.onmessage({ data: { rate, muted } })
  for (const key of Array.isArray(digit) ? digit : [digit])
    node.port.onmessage({ data: { digit: key } })
  for (let i = 0; i < 375 * seconds; i++) node.process([[new Float32Array(128).fill(0.8)]])
  const samples = packets.flatMap((packet) => {
    const view = new DataView(packet)
    return Array.from(
      { length: packet.byteLength / 2 },
      (_, i) => view.getInt16(i * 2, true) / 32768,
    )
  })
  assert.equal(packets.length, 25 * seconds)
  assert.equal(samples.length, rate * seconds)
  return { samples, node }
}
function amplitude(samples, rate, frequency) {
  let real = 0
  let imaginary = 0
  samples.forEach((value, index) => {
    real += value * Math.cos((2 * Math.PI * frequency * index) / rate)
    imaginary += value * Math.sin((2 * Math.PI * frequency * index) / rate)
  })
  return (2 * Math.hypot(real, imaginary)) / samples.length
}
const rows = ['123', '456', '789', '*0#']
for (const rate of [8000, 16000]) {
  for (const [row, keys] of rows.entries()) {
    for (const [column, digit] of [...keys].entries()) {
      const { samples } = capture(rate, digit)
      const tone = samples.slice(Math.round(rate * 0.01), Math.round(rate * 0.39))
      for (const frequency of [[697, 770, 852, 941][row], [1209, 1336, 1477][column]]) {
        assert.ok(
          amplitude(tone, rate, frequency) > 0.23,
          `${rate}Hz key ${digit}: ${frequency}Hz missing`,
        )
      }
      assert.ok(samples.every((value) => Math.abs(value) <= 0.501))
      assert.ok(
        samples.slice(Math.round(rate * 0.4)).every((value) => value === 0),
        'mute and interdigit silence',
      )
    }
  }
  const { samples } = capture(rate, '#', false)
  assert.ok(
    samples.slice(Math.round(rate * 0.4), Math.round(rate * 0.56)).every((value) => value === 0),
  )
  assert.ok(
    samples.slice(Math.round(rate * 0.57)).every((value) => value > 0.79),
    'microphone resumes',
  )
  const { node } = capture(rate, 'invalid')
  assert.equal(node.tones.length, 0)
  for (let i = 0; i < 100; i++) node.port.onmessage({ data: { digit: '1' } })
  assert.equal(node.tones.length, 16, 'bounded tone queue')
  const queued = capture(rate, ['1', '2'], false, 2).samples
  const secondTone = queued.slice(Math.round(rate * 0.57), Math.round(rate * 0.95))
  assert.ok(amplitude(secondTone, rate, 697) > 0.23)
  assert.ok(amplitude(secondTone, rate, 1336) > 0.23)
  for (const [from, to] of [
    [0.4, 0.56],
    [0.96, 1.12],
  ])
    assert.ok(queued.slice(Math.round(rate * from), Math.round(rate * to)).every((v) => v === 0))
  assert.ok(queued.slice(Math.round(rate * 1.13)).every((v) => v > 0.79))
}
console.log(
  'DTMF passed: 12 keys × 8/16kHz, frequency pairs, duration/gap, mute, microphone recovery, bounded queue',
)
