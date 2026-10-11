import { QueryClient } from '@tanstack/react-query'
export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1 }, mutations: { retry: false } },
})
export const stateKey = ['device-state']
type Pending = {
  resolve: () => void
  reject: (error: Error) => void
  timer: ReturnType<typeof setTimeout>
}
class Connection {
  private socket: WebSocket | null = null
  private retry: ReturnType<typeof setTimeout> | null = null
  private heartbeat: ReturnType<typeof setInterval> | null = null
  private attempts = 0
  private active = false
  private status = 'connecting'
  private listeners = new Set<() => void>()
  getStatus = () => this.status
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }
  private setStatus(status: string) {
    this.status = status
    for (const listener of this.listeners) listener()
  }
  private pending = new Map<string, Pending>()
  start() {
    this.active = true
    if (this.retry) clearTimeout(this.retry)
    this.connect()
  }
  stop() {
    this.active = false
    if (this.retry) clearTimeout(this.retry)
    if (this.heartbeat) clearInterval(this.heartbeat)
    const socket = this.socket
    this.socket = null
    socket?.close()
    this.failPending()
  }
  private failPending() {
    for (const item of this.pending.values()) {
      clearTimeout(item.timer)
      item.reject(new Error('连接中断，请查看发送记录；请勿重复发送。'))
    }
    this.pending.clear()
  }
  private connect() {
    if (!this.active) return
    this.setStatus('connecting')
    const socket = new WebSocket(
      `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`,
    )
    this.socket = socket
    socket.onopen = () => {
      if (this.socket !== socket) return
      this.attempts = 0
      this.setStatus('connected')
      this.heartbeat = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN)
          socket.send(JSON.stringify({ action: 'ping', request_id: crypto.randomUUID() }))
      }, 20000)
    }
    socket.onmessage = (event) => {
      if (this.socket !== socket) return
      this.setStatus('connected')
      let message
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      if (message.type === 'snapshot') queryClient.setQueryData(stateKey, message)
      const pending = this.pending.get(message.request_id)
      if (pending && ['command_ack', 'command_error'].includes(message.type)) {
        clearTimeout(pending.timer)
        this.pending.delete(message.request_id)
        if (message.type === 'command_error') pending.reject(new Error(message.error))
        else pending.resolve()
      }
    }
    socket.onclose = () => {
      if (this.socket !== socket) return
      this.setStatus('disconnected')
      if (this.heartbeat) clearInterval(this.heartbeat)
      this.failPending()
      if (this.active)
        this.retry = setTimeout(() => this.connect(), Math.min(1000 * 2 ** this.attempts++, 10000))
    }
    socket.onerror = () => socket.close()
  }
  command(action: string, data: Record<string, string> = {}) {
    if (this.socket?.readyState !== WebSocket.OPEN)
      return Promise.reject(new Error('服务连接已断开，正在重连'))
    const request_id = crypto.randomUUID()
    return new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(request_id)
        reject(new Error('请求确认超时，请查看记录；请勿重复发送。'))
      }, 8000)
      this.pending.set(request_id, { resolve, reject, timer })
      this.socket!.send(JSON.stringify({ action, request_id, ...data }))
    })
  }
}
export const connection = new Connection()

export function createCallSocket() {
  const socket = new WebSocket(
    `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/call`,
  )
  socket.binaryType = 'arraybuffer'
  return socket
}

export function sendCallCommand(
  socket: WebSocket | null,
  action: string,
  data: Record<string, string>,
) {
  if (socket?.readyState !== WebSocket.OPEN) throw new Error('通话服务已断开')
  socket.send(JSON.stringify({ action, request_id: crypto.randomUUID(), ...data }))
}

export type AgentConfig = {
  enabled: boolean
  provider: 'aihubmix' | 'openai'
  model: string
  voice: string
  greeting: string
  instructions: string
  has_api_key: boolean
  key_source: 'local' | 'environment' | 'none'
  ready: boolean
}
export const agentSettingsKey = ['agent-settings']
async function agentRequest(path: string, init?: RequestInit) {
  const response = await fetch(path, init)
  const data = await response.json()
  if (!response.ok)
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Agent配置请求失败')
  return data
}
export function getAgentSettings(): Promise<AgentConfig> {
  return agentRequest('/api/agent')
}
export function saveAgentSettings(data: {
  enabled: boolean
  provider: 'aihubmix' | 'openai'
  model: string
  voice: string
  greeting: string
  instructions: string
  api_key?: string
  clear_api_key?: boolean
}): Promise<AgentConfig> {
  return agentRequest('/api/agent', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}
export function testAgentConnection(): Promise<{ message: string }> {
  return agentRequest('/api/agent/test', { method: 'POST' })
}

export type DingTalkConfig = {
  enabled: boolean
  has_webhook: boolean
  has_secret: boolean
  ready: boolean
  deliveries: {
    message_id: string
    state: string
    error: string | null
    updated_at: number
  }[]
}
export const dingtalkSettingsKey = ['dingtalk-settings']
async function dingtalkRequest(path: string, init?: RequestInit) {
  const response = await fetch(path, init)
  const data = await response.json()
  if (!response.ok)
    throw new Error(typeof data.detail === 'string' ? data.detail : '钉钉推送配置请求失败')
  return data
}
export function getDingTalkSettings(): Promise<DingTalkConfig> {
  return dingtalkRequest('/api/dingtalk')
}
export function saveDingTalkSettings(data: {
  enabled: boolean
  webhook?: string
  secret?: string
  clear_credentials?: boolean
}): Promise<DingTalkConfig> {
  return dingtalkRequest('/api/dingtalk', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}
export function testDingTalkPush(): Promise<{ message: string }> {
  return dingtalkRequest('/api/dingtalk/test', { method: 'POST' })
}
