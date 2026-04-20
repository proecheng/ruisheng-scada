export type WSMessage =
  | { type: 'realtime'; dev_number: string; point_id: number; value: number; ts: string }
  | {
      type: 'alarm'
      event_id: number
      dev_number: string
      alarm_name: string
      value: number
      limit: number
      ts: string
    }
  | { type: 'alarm_reset'; event_id: number; dev_number: string; ts: string }
  | {
      type: 'control_result'
      cmd_id: string
      status: 'success' | 'failed' | 'timeout' | 'cancelled'
      at: string
      reason?: string
    }
  | {
      type: 'device_state'
      dev_number: string
      state: 'online' | 'offline' | 'warning'
      at: string
    }
  | { type: 'ping'; ts: string }

export type WSState = 'connecting' | 'open' | 'closing' | 'closed' | 'reconnecting'

export type WSListener = (msg: WSMessage) => void
