import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import type { InputRef } from 'antd'
import { Alert, App, Badge, Button, Form, Input, Progress, Space, Tag, Modal } from 'antd'
import {
  AudioMutedOutlined,
  AudioOutlined,
  PhoneOutlined,
  StopOutlined,
  DeleteOutlined,
  ArrowLeftOutlined,
  RedoOutlined,
  RobotOutlined,
} from '@ant-design/icons'
import type { Snapshot } from './types'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import CallHistory, { callHistoryKey } from './CallHistory'
import { agentSettingsKey, getAgentSettings, createCallSocket, sendCallCommand } from './connection'
import { startRingback } from './ringback'

type CallState = {
  state: string
  call_id?: string
  sample_rate?: number
  to?: string
  mode?: 'browser' | 'agent'
  agent_status?: string
  agent_error?: string
  reason?: string
  up_bytes?: number
  down_bytes?: number
  device_up_bytes?: number
  dropped_frames?: number
  recording_error?: string
}
const labels: Record<string, string> = {
  idle: '未通话',
  incoming: '来电待接听',
  answering: '正在接听',
  agent_connecting: '正在连接 Agent',
  dialing: '拨号中',
  connected: '已接通，建立音频中',
  streaming: '实时通话中',
  hanging_up: '正在挂断',
  ended: '通话已结束',
  failed: '拨号失败',
  unknown: '通话结果未知',
}
const terminal = new Set(['idle', 'ended', 'failed', 'unknown'])
const keypad = [
  ['1', ''],
  ['2', 'ABC'],
  ['3', 'DEF'],
  ['4', 'GHI'],
  ['5', 'JKL'],
  ['6', 'MNO'],
  ['7', 'PQRS'],
  ['8', 'TUV'],
  ['9', 'WXYZ'],
  ['*', ''],
  ['0', ''],
  ['#', ''],
]

export default function CallPanel({
  device,
  ready,
  onAnswer,
  onActivity,
}: {
  device: Snapshot['device']
  ready: boolean
  onAnswer: () => void
  onActivity: (active: boolean) => void
}) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const agentSettings = useQuery({ queryKey: agentSettingsKey, queryFn: getAgentSettings })
  const [form] = Form.useForm()
  const [call, setCall] = useState<CallState>({ state: 'idle' })
  useEffect(() => {
    onActivity(!terminal.has(call.state))
  }, [call.state, onActivity])
  const [connected, setConnected] = useState(false)
  const [socketGeneration, setSocketGeneration] = useState(0)
  const [preparing, setPreparing] = useState(false)
  const [muted, setMuted] = useState(false)
  const [level, setLevel] = useState(0)
  const [heard, setHeard] = useState(0)
  const [localSent, setLocalSent] = useState(0)
  const [error, setError] = useState('')
  const [digits, setDigits] = useState('')
  const [lastNumber, setLastNumber] = useState('')
  const number = Form.useWatch('to', form) ?? ''
  const numberInput = useRef<InputRef>(null)
  const socket = useRef<WebSocket | null>(null)
  const callRef = useRef<CallState>({ state: 'idle' })
  const context = useRef<AudioContext | null>(null)
  const microphone = useRef<MediaStream | null>(null)
  const capture = useRef<AudioWorkletNode | null>(null)
  const sources = useRef(new Set<AudioBufferSourceNode>())
  const sequence = useRef(0)
  const playbackTime = useRef(0)
  const playingSequence = useRef(-1)
  const stats = useRef({ up: 0, down: 0, last: 0 })
  const mine = useRef<string | null>(null)
  const audioGeneration = useRef(0)
  const ringback = useRef<(() => void) | null>(null)

  function stopRingback() {
    ringback.current?.()
    ringback.current = null
  }

  function releaseAudio() {
    audioGeneration.current++
    stopRingback()
    microphone.current?.getTracks().forEach((track) => track.stop())
    microphone.current = null
    capture.current?.disconnect()
    capture.current = null
    sources.current.forEach((source) => source.stop())
    sources.current.clear()
    void context.current?.close()
    context.current = null
    setLevel(0)
  }
  useEffect(() => {
    let reconnect: ReturnType<typeof setTimeout> | undefined
    const ws = createCallSocket()
    socket.current = ws
    ws.onopen = () => {
      if (socket.current === ws) setConnected(true)
    }
    ws.onclose = () => {
      if (socket.current !== ws) return
      setConnected(false)
      releaseAudio()
      if (!terminal.has(callRef.current.state))
        setError(
          callRef.current.state === 'incoming'
            ? '来电通知连接已断开，正在重连。'
            : '通话连接已断开；设备将自动挂断，不会重拨。',
        )
      setPreparing(false)
      mine.current = null
      reconnect = setTimeout(() => setSocketGeneration((value) => value + 1), 1500)
    }
    ws.onmessage = ({ data }) => {
      if (socket.current !== ws) return
      if (typeof data !== 'string') {
        const audio = context.current
        if (!audio || callRef.current.state !== 'streaming' || data.byteLength < 8) return
        const view = new DataView(data)
        const rate = view.getUint16(0, true)
        const packetSequence = view.getUint32(2, true)
        if (![8000, 16000].includes(rate) || packetSequence <= playingSequence.current) return
        playingSequence.current = packetSequence
        const samples = (data.byteLength - 6) / 2
        const buffer = audio.createBuffer(1, samples, rate)
        const channel = buffer.getChannelData(0)
        for (let i = 0; i < samples; i++) channel[i] = view.getInt16(6 + i * 2, true) / 32768
        if (playbackTime.current > audio.currentTime + 0.35) return
        const source = audio.createBufferSource()
        source.buffer = buffer
        source.connect(audio.destination)
        sources.current.add(source)
        source.onended = () => sources.current.delete(source)
        const at = Math.max(audio.currentTime + 0.06, playbackTime.current)
        source.start(at)
        playbackTime.current = at + samples / rate
        stats.current.down += samples * 2
        setHeard(stats.current.down)
        return
      }
      const event = JSON.parse(data)
      if (event.type === 'command_error') {
        void queryClient.invalidateQueries({ queryKey: callHistoryKey })
        setError(event.error)
        releaseAudio()
        setPreparing(false)
      } else if (event.type === 'call_state') {
        if (event.state !== callRef.current.state || event.call_id !== callRef.current.call_id)
          void queryClient.invalidateQueries({ queryKey: callHistoryKey })
        if (
          event.state === 'incoming' &&
          (event.call_id !== callRef.current.call_id || terminal.has(callRef.current.state))
        )
          setError('')
        callRef.current = event
        setCall(event)
        if (event.mode === 'agent' && event.state !== 'incoming') setPreparing(false)
        if (event.state === 'dialing' && event.call_id === mine.current && context.current) {
          if (!ringback.current) ringback.current = startRingback(context.current)
        } else stopRingback()
        if (event.call_id === mine.current && event.state === 'streaming') {
          if (capture.current) {
            // Only reset the resampler when the negotiated rate changes.
            const node = capture.current as AudioWorkletNode & { telephonyRate?: number }
            if (node.telephonyRate !== event.sample_rate) {
              node.telephonyRate = event.sample_rate
              node.port.postMessage({ rate: event.sample_rate })
            }
          }
        }
        if (terminal.has(event.state)) {
          setDigits('')
          releaseAudio()
          setPreparing(false)
          mine.current = null
        }
      }
    }
    return () => {
      clearTimeout(reconnect)
      ws.close()
      releaseAudio()
      socket.current = null
    }
  }, [socketGeneration])

  async function dial({ to }: { to: string }, incomingId?: string) {
    if (socket.current?.readyState !== WebSocket.OPEN) return
    setError('')
    setPreparing(true)
    const generation = audioGeneration.current
    const ws = socket.current
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      })
      if (
        generation !== audioGeneration.current ||
        socket.current !== ws ||
        ws.readyState !== WebSocket.OPEN
      ) {
        stream.getTracks().forEach((track) => track.stop())
        throw new Error('通话状态已变化，请重新操作')
      }
      microphone.current = stream
      const audio = new AudioContext()
      context.current = audio
      await audio.resume()
      await audio.audioWorklet.addModule(new URL('./pcm-worklet.js', import.meta.url))
      if (generation !== audioGeneration.current) throw new Error('通话已结束')
      if (
        incomingId &&
        (callRef.current.state !== 'incoming' || callRef.current.call_id !== incomingId)
      )
        throw new Error('来电已结束或已由其他页面接听')
      if (!incomingId && !terminal.has(callRef.current.state)) throw new Error('设备已有通话')
      const node = new AudioWorkletNode(audio, 'pcm-capture')
      capture.current = node
      audio.createMediaStreamSource(stream).connect(node)
      node.connect(audio.destination)
      sequence.current = 0
      playingSequence.current = -1
      playbackTime.current = 0
      stats.current = { up: 0, down: 0, last: 0 }
      setHeard(0)
      setLocalSent(0)
      setMuted(false)
      node.port.onmessage = ({ data }: MessageEvent<ArrayBuffer>) => {
        if (
          callRef.current.state !== 'streaming' ||
          mine.current !== callRef.current.call_id ||
          socket.current?.readyState !== WebSocket.OPEN
        )
          return
        if (socket.current.bufferedAmount > 16000) return
        const packet = new Uint8Array(4 + data.byteLength)
        new DataView(packet.buffer).setUint32(0, sequence.current++, true)
        packet.set(new Uint8Array(data), 4)
        socket.current.send(packet)
        stats.current.up += data.byteLength
        if (performance.now() - stats.current.last > 200) {
          const view = new DataView(data)
          let sum = 0
          for (let i = 0; i < data.byteLength; i += 2) sum += (view.getInt16(i, true) / 32768) ** 2
          setLevel(Math.min(100, Math.round(Math.sqrt(sum / (data.byteLength / 2)) * 400)))
          setLocalSent(stats.current.up)
          stats.current.last = performance.now()
        }
      }
      const callId = incomingId ?? crypto.randomUUID()
      mine.current = callId
      sendCallCommand(
        socket.current,
        incomingId ? 'call_answer' : 'call_dial',
        incomingId ? { call_id: callId } : { request_id: callId, to },
      )
      if (incomingId) onAnswer()
      else setLastNumber(to)
      setDigits('')
      setPreparing(false)
    } catch (cause) {
      if (generation === audioGeneration.current) releaseAudio()
      setPreparing(false)
      const text = cause instanceof Error ? cause.message : '无法启动麦克风'
      setError(text)
      message.error(text)
    }
  }
  function answerWithAgent() {
    setError('')
    setPreparing(true)
    try {
      mine.current = call.call_id ?? null
      sendCallCommand(socket.current, 'call_agent_answer', { call_id: call.call_id! })
      onAnswer()
    } catch (cause) {
      setPreparing(false)
      mine.current = null
      setError(cause instanceof Error ? cause.message : 'Agent接听失败')
    }
  }
  const active = !terminal.has(call.state)
  const canSendTone =
    connected &&
    call.mode !== 'agent' &&
    call.state === 'streaming' &&
    mine.current === call.call_id
  function editNumber(text: string, remove = false) {
    if (active || preparing) return
    const input = numberInput.current?.input
    const start = input?.selectionStart ?? number.length
    const end = input?.selectionEnd ?? number.length
    const from = remove && start === end ? Math.max(0, start - 1) : start
    const next = number.slice(0, from) + text + number.slice(end)
    if (next.length > 21) return
    form.setFieldValue('to', next)
    numberInput.current?.focus({ cursor: 'end' })
    requestAnimationFrame(() => input?.setSelectionRange(from + text.length, from + text.length))
  }
  function pressDigit(digit: string) {
    if (active) {
      if (!canSendTone || !capture.current) return
      capture.current.port.postMessage({ digit })
      setDigits((value) => (value + digit).slice(-32))
    } else if (/^\d$/.test(digit)) editNumber(digit)
  }
  function keypadKeyboard(event: KeyboardEvent<HTMLDivElement>) {
    if (event.ctrlKey || event.metaKey || event.altKey || event.repeat) return
    if (/^[0-9*#]$/.test(event.key)) {
      event.preventDefault()
      pressDigit(event.key)
    } else if (event.key === 'Backspace' && !active) {
      event.preventDefault()
      editNumber('', true)
    }
  }
  return (
    <>
      <Modal
        title="设备来电"
        open={call.state === 'incoming'}
        closable={false}
        maskClosable={false}
        keyboard={false}
        footer={null}
      >
        <p role="status" className="break-all font-mono text-xl">
          {call.to || '未知号码'}
        </p>
        <p className="text-sm text-slate-500">接听后使用电脑麦克风和耳机通话。请允许麦克风权限。</p>
        {error && <Alert className="mb-4!" type="error" showIcon title={error} />}
        <div className="flex flex-wrap gap-3">
          <Button
            type="primary"
            icon={<PhoneOutlined />}
            loading={preparing}
            disabled={!ready || !connected}
            onClick={() => void dial({ to: call.to ?? '' }, call.call_id)}
          >
            接听
          </Button>
          <Button
            icon={<RobotOutlined />}
            disabled={!ready || !connected || preparing || !agentSettings.data?.ready}
            onClick={answerWithAgent}
          >
            Agent接听
          </Button>
          <Button
            danger
            icon={<StopOutlined />}
            disabled={!connected || preparing}
            onClick={() => {
              try {
                sendCallCommand(socket.current, 'call_reject', { call_id: call.call_id! })
              } catch (cause) {
                setError(cause instanceof Error ? cause.message : '拒接失败')
              }
            }}
          >
            拒接
          </Button>
        </div>
        <p className="mb-0 mt-3 text-xs text-slate-500">
          {agentSettings.data?.ready
            ? 'Agent接听按已保存的 AGENT.md 回复，无需电脑麦克风。'
            : '如需 Agent接听，请先在「AGENT设置」中启用并保存配置。'}
        </p>
      </Modal>
      <section
        id="call-workspace"
        className="mb-6 scroll-mt-4 rounded-2xl border border-[#dce5ed] bg-white p-6 max-[740px]:p-5"
      >
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="m-0 flex items-center gap-2 text-base font-semibold">
            <PhoneOutlined /> 实时通话
          </h2>
          <Space>
            <Badge
              status={connected ? 'success' : 'default'}
              text={connected ? '音频服务已连接' : '音频服务未连接'}
            />
            <Tag color={active ? 'blue' : 'default'}>{labels[call.state] || call.state}</Tag>
          </Space>
        </div>
        <p className="mb-4 text-xs text-[#6d8195]">
          用电脑麦克风和耳机通话。来电时可选择接听或拒接，关闭接听或拨号的页面会挂断。建议戴耳机。接通后自动保存双向录音。
        </p>
        {active && call.mode === 'agent' && (
          <Alert
            className="mb-4!"
            showIcon
            type="info"
            title={`Agent通话 · ${call.agent_status === 'speaking' ? '正在回复' : call.agent_status === 'listening' ? '正在聆听' : call.state === 'agent_connecting' ? '连接 Realtime中' : '准备通话'}`}
            description="电话由 Agent按场景回复，电脑麦克风不参与。可随时挂断。"
          />
        )}
        {call.agent_error && (
          <Alert className="mb-4!" showIcon type="error" title="Agent连接或音频异常，已请求挂断" />
        )}
        {active && call.to && <p className="break-all font-mono text-sm">{call.to}</p>}
        {call.state === 'dialing' && mine.current === call.call_id && (
          <p role="status" className="mb-4 text-xs text-[#6d8195]">
            等待接通 · 正在播放本地拨号提示音
          </p>
        )}
        {!device?.call?.live_supported && (
          <Alert className="mb-4!" type="warning" showIcon title="设备实时通话程序尚未就绪" />
        )}
        {error && <Alert className="mb-4!" type="error" showIcon title={error} />}
        {call.recording_error && (
          <Alert
            className="mb-4!"
            type="warning"
            showIcon
            title="录音保存异常，通话仍可继续；这次录音可能不完整。"
          />
        )}
        <Form
          form={form}
          layout="inline"
          onFinish={dial}
          className="flex-wrap! gap-3! [&_.ant-form-item]:mr-0!"
        >
          <Form.Item
            name="to"
            rules={[
              { required: true, message: '请输入号码' },
              { pattern: /^\+?\d{5,20}$/, message: '请输入有效号码' },
            ]}
          >
            <Input
              ref={numberInput}
              aria-label="拨号号码"
              placeholder="输入拨号号码"
              inputMode="tel"
              autoComplete="off"
              disabled={active || preparing}
            />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            icon={<PhoneOutlined />}
            loading={preparing}
            disabled={!ready || !connected || active || !device?.call?.live_supported}
          >
            拨号
          </Button>
          <Button
            danger
            icon={<StopOutlined />}
            disabled={!active || mine.current !== call.call_id}
            onClick={() => {
              try {
                sendCallCommand(socket.current, 'call_hangup', { call_id: call.call_id! })
              } catch (cause) {
                setError(cause instanceof Error ? cause.message : '挂断失败')
              }
            }}
          >
            挂断
          </Button>
          <Button
            icon={muted ? <AudioMutedOutlined /> : <AudioOutlined />}
            disabled={!active || call.mode === 'agent' || mine.current !== call.call_id}
            onClick={() => {
              setMuted(!muted)
              capture.current?.port.postMessage({ muted: !muted })
            }}
          >
            {muted ? '取消静音' : '麦克风静音'}
          </Button>
        </Form>
        <div className="mt-5 grid grid-cols-[minmax(220px,300px)_1fr] gap-6 max-[740px]:grid-cols-1">
          <div
            role="group"
            aria-label="电话拨号键盘"
            tabIndex={0}
            onKeyDown={keypadKeyboard}
            className="rounded-xl bg-slate-50 p-3 outline-offset-4 focus-visible:outline-2 focus-visible:outline-blue-500"
          >
            <div className="grid grid-cols-3 gap-2">
              {keypad.map(([digit, letters]) => (
                <Button
                  key={digit}
                  aria-label={`按键 ${digit}`}
                  disabled={preparing || (active ? !canSendTone : digit === '*' || digit === '#')}
                  onClick={() => pressDigit(digit)}
                  className="h-16! flex-col! gap-0! rounded-lg!"
                >
                  <span className="text-2xl font-medium leading-7">{digit}</span>
                  <span className="min-h-3 text-[10px] leading-3 tracking-widest text-slate-400">
                    {letters}
                  </span>
                </Button>
              ))}
            </div>
            <div className="mt-2 grid grid-cols-3 gap-2">
              <Button
                aria-label="输入国际区号加号"
                disabled={active || preparing || number.length > 0}
                onClick={() => editNumber('+')}
              >
                +
              </Button>
              <Button
                aria-label="删除一位号码"
                icon={<ArrowLeftOutlined />}
                disabled={active || preparing || !number}
                onClick={() => editNumber('', true)}
              />
              <Button
                aria-label="清空号码"
                icon={<DeleteOutlined />}
                disabled={active || preparing || !number}
                onClick={() => form.setFieldValue('to', '')}
              />
            </div>
          </div>
          <div className="flex flex-col justify-center gap-3">
            <h3 className="m-0 text-sm font-medium">{active ? '通话按键' : '输入号码'}</h3>
            <p className="m-0 text-xs leading-6 text-[#6d8195]">
              {active
                ? call.mode === 'agent'
                  ? 'Agent通话由场景指令控制回复，不使用电脑麦克风或按键音。'
                  : canSendTone
                    ? '点击按键向对方发送按键音，可操作“按 1 转人工”等语音菜单。麦克风静音时仍可使用。'
                    : mine.current !== call.call_id
                      ? '请在发起这通电话的页面操作按键。'
                      : '接通并建立音频后即可使用按键。'
                : '点击数字输入号码，再点击拨号。可删除、清空或添加国际区号 +；* 和 # 在通话接通后可用。'}
            </p>
            <p className="m-0 text-xs text-[#6d8195]">
              键盘聚焦后，可直接按数字、*、#；拨号前按退格删除。
            </p>
            {active && (
              <div
                aria-live="polite"
                className="min-h-10 break-all rounded-lg border border-slate-200 px-3 py-2 font-mono text-base tracking-widest"
              >
                {digits || '尚未发送按键'}
              </div>
            )}
            {!active && lastNumber && (
              <Button
                className="self-start"
                icon={<RedoOutlined />}
                disabled={preparing}
                onClick={() => form.setFieldValue('to', lastNumber)}
              >
                填入上次号码
              </Button>
            )}
          </div>
        </div>
        <div className="mt-5 grid grid-cols-[minmax(120px,200px)_1fr] items-center gap-6 max-[740px]:grid-cols-1 max-[740px]:gap-2">
          <div>
            {call.mode === 'agent' ? (
              <span className="text-xs text-[#6d8195]">
                Agent语音 ·{' '}
                {call.agent_status === 'speaking' ? '正在回复' : active ? '正在聆听' : '已结束'}
              </span>
            ) : (
              <>
                <span className="text-xs text-[#6d8195]">麦克风音量</span>
                <Progress percent={level} showInfo={false} size="small" />
              </>
            )}
          </div>
          <div className="text-xs leading-6 text-[#6d8195]">
            {call.mode === 'agent' ? 'Agent发出' : '网页发出'}{' '}
            {((call.mode === 'agent' ? (call.up_bytes ?? 0) : localSent) / 1024).toFixed(1)} KB ·{' '}
            {call.mode === 'agent' ? 'Agent收到' : '网页收到'}{' '}
            {((call.mode === 'agent' ? (call.down_bytes ?? 0) : heard) / 1024).toFixed(1)} KB ·
            设备已写入 {((call.device_up_bytes ?? 0) / 1024).toFixed(1)} KB
            {call.sample_rate ? ` · ${call.sample_rate / 1000} kHz` : ''}
            {call.reason ? ` · ${call.reason}` : ''}
          </div>
        </div>
      </section>
      <CallHistory active={active} />
    </>
  )
}
