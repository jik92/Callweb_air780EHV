import assert from 'node:assert/strict'
import { ringbackSamples, startRingback } from '../src/ringback.ts'
for (const rate of [8000, 16000, 48000]) {
  const samples = ringbackSamples(rate)
  assert.equal(samples.length, rate * 2)
  assert.ok(samples.every((value) => Math.abs(value) <= 0.121))
  assert.ok(samples.slice(rate * 0.2, rate * 0.4).every((value) => value === 0))
  assert.ok(samples.slice(rate * 0.6).every((value) => value === 0))
  for (const from of [0.02, 0.42]) {
    const burst = samples.slice(Math.round(from * rate), Math.round((from + 0.16) * rate))
    let real = 0,
      imaginary = 0
    burst.forEach((value, i) => {
      real += value * Math.cos((2 * Math.PI * 450 * i) / rate)
      imaginary += value * Math.sin((2 * Math.PI * 450 * i) / rate)
    })
    assert.ok((2 * Math.hypot(real, imaginary)) / burst.length > 0.11)
  }
}
const lifecycle = []
const source = {
  connect: (destination) => lifecycle.push(destination),
  start: () => lifecycle.push('start'),
  stop: () => lifecycle.push('stop'),
  disconnect: () => lifecycle.push('disconnect'),
}
const stop = startRingback({
  sampleRate: 16000,
  destination: 'speaker',
  createBuffer: () => ({ copyToChannel: () => {} }),
  createBufferSource: () => source,
})
assert.equal(source.loop, true)
stop()
assert.deepEqual(lifecycle, ['speaker', 'start', 'stop', 'disconnect'])
console.log(
  'Ringback passed: 450Hz bursts/gaps, bounded amplitude, speaker-only routing and stop cleanup',
)
