/* Stream mono S16LE frames at the negotiated telephony rate, 40 ms per frame. */
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super()
    this.rate = 16000
    this.muted = false
    this.phase = 0
    this.sum = 0
    this.count = 0
    this.samples = []
    this.tones = []
    this.tone = null
    this.port.onmessage = ({ data }) => {
      if (data.rate) {
        this.rate = data.rate
        this.phase = 0
        this.sum = 0
        this.count = 0
        this.samples = []
      }
      if (typeof data.muted === 'boolean') this.muted = data.muted
      if (typeof data.digit === 'string' && this.tones.length < 16) {
        const keys = ['123', '456', '789', '*0#']
        const row = keys.findIndex((line) => line.includes(data.digit) && data.digit.length === 1)
        if (row >= 0) {
          const column = keys[row].indexOf(data.digit)
          this.tones.push({ low: [697, 770, 852, 941][row], high: [1209, 1336, 1477][column] })
        }
      }
    }
  }
  process(inputs) {
    const input = inputs[0]?.[0]
    for (const sample of input ?? new Float32Array(128)) {
      this.sum += sample
      this.count++
      this.phase += this.rate
      if (this.phase >= sampleRate) {
        this.phase -= sampleRate
        let value = this.muted ? 0 : Math.max(-1, Math.min(1, this.sum / this.count))
        if (!this.tone && this.tones.length) this.tone = { ...this.tones.shift(), at: 0 }
        if (this.tone) {
          const { low, high, at } = this.tone
          // Keep the pair steady across several speech-codec frames for IVR detection.
          const duration = Math.round(this.rate * 0.4)
          const ramp = this.rate * 0.005
          const envelope = Math.max(0, Math.min(1, at / ramp, (duration - at) / ramp))
          // Replace microphone speech during the digit and 160ms gap, even when muted.
          value =
            at < duration
              ? 0.25 *
                envelope *
                (Math.sin((2 * Math.PI * low * at) / this.rate) +
                  Math.sin((2 * Math.PI * high * at) / this.rate))
              : 0
          this.tone.at++
          if (this.tone.at >= Math.round(this.rate * 0.56)) this.tone = null
        }
        this.samples.push(Math.round(value * (value < 0 ? 32768 : 32767)))
        this.sum = 0
        this.count = 0
        if (this.samples.length === this.rate / 25) {
          const buffer = new ArrayBuffer(this.samples.length * 2)
          const view = new DataView(buffer)
          this.samples.forEach((value, index) => view.setInt16(index * 2, value, true))
          this.port.postMessage(buffer, [buffer])
          this.samples = []
        }
      }
    }
    return true
  }
}
registerProcessor('pcm-capture', PcmCapture)
