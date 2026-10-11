/** Local waiting sound only. It does not establish remote ringing status. */
export function ringbackSamples(rate: number) {
  const samples = new Float32Array(rate * 2)
  for (let i = 0; i < samples.length; i++) {
    const time = i / rate
    const burst = time < 0.2 ? time : time >= 0.4 && time < 0.6 ? time - 0.4 : -1
    if (burst >= 0) {
      const envelope = Math.max(0, Math.min(1, burst / 0.008, (0.2 - burst) / 0.008))
      samples[i] = 0.12 * envelope * Math.sin(2 * Math.PI * 450 * time)
    }
  }
  return samples
}
export function startRingback(context: AudioContext) {
  const buffer = context.createBuffer(1, context.sampleRate * 2, context.sampleRate)
  buffer.copyToChannel(ringbackSamples(context.sampleRate), 0)
  const source = context.createBufferSource()
  source.buffer = buffer
  source.loop = true
  source.connect(context.destination)
  source.start()
  return () => {
    source.stop()
    source.disconnect()
  }
}
