export interface Sms {
  id: string
  direction: 'incoming' | 'outgoing'
  phone: string
  content: string
  timestamp: number
  state: 'received' | 'queued' | 'sending' | 'submitted' | 'failed' | 'unknown'
  error: string | null
  sim_id: string
}
export interface Snapshot {
  type: 'snapshot'
  bridge: {
    connected: boolean
    ready: boolean
    port: string | null
    error: string | null
    last_seen: number | null
  }
  device: {
    project: string
    version: string
    call?: { ready: boolean; audio_ready: boolean; busy: boolean; live_supported?: boolean }
    mobile: {
      sim_ready: boolean
      sms_ready: boolean
      number: string
      number_source?: 'sim' | 'manual' | 'unavailable'
      manual_number?: string
      number_confirmed_at?: number
      iccid: string
      imsi: string
      sim_slot: number
      net_status: number
      csq: number
      rssi: number
      rsrp: number
      rsrq: number
      snr: number
      serving_cell?: { mcc: number; mnc: number; band: number; pci: number }
    }
  } | null
  messages: Sms[]
}
