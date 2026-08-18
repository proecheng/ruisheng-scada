import { onMounted, onUnmounted } from 'vue'
import { WSClient } from '@/ws/client'
import type { WSMessage } from '@/ws/types'
import { useWsStore } from '@/stores/ws'
import { useAlarmsStore } from '@/stores/alarms'
import { useDevicesStore } from '@/stores/devices'
import { useDiagStore } from '@/stores/diag'
import { getAuthToken } from '@/api/client'

let singleton: WSClient | null = null
let stateSyncTimer: ReturnType<typeof setInterval> | null = null

function stopStateSync(): void {
  if (stateSyncTimer !== null) {
    clearInterval(stateSyncTimer)
    stateSyncTimer = null
  }
}

export function useWsConnection() {
  const wsStore = useWsStore()
  const alarms = useAlarmsStore()
  const devices = useDevicesStore()
  const diag = useDiagStore()
  let ownsConnection = false

  onMounted(() => {
    if (singleton) return
    const base = import.meta.env.VITE_WS_BASE ?? '/ws'
    const token = getAuthToken()
    const url = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}${base}${token ? `?token=${encodeURIComponent(token)}` : ''}`
    const client = new WSClient(url)
    client.on((m: WSMessage) => {
      wsStore.pushMessage(m)
      diag.record({
        at: new Date().toISOString(),
        kind: 'ws',
        label: `ws:${m.type}`,
        detail: JSON.stringify(m).slice(0, 200),
      })
      if (m.type === 'alarm') {
        alarms.push({
          event_id: m.event_id,
          dev_number: m.dev_number,
          alarm_name: m.alarm_name,
          value: m.value,
          limit: m.limit,
          ts: m.ts,
          acked: false,
        })
      } else if (m.type === 'device_state') {
        devices.updateState(m.dev_number, m.state)
      }
    })
    try {
      client.connect()
    } catch {
      client.close()
      wsStore.setState('closed')
      return
    }
    singleton = client
    ownsConnection = true
    stopStateSync()
    stateSyncTimer = setInterval(() => wsStore.setState(client.state), 500)
  })

  onUnmounted(() => {
    if (!ownsConnection) return
    stopStateSync()
    singleton?.close()
    singleton = null
    ownsConnection = false
    wsStore.setState('closed')
  })

  return {
    send: (msg: unknown) => singleton?.send(msg),
    close: () => {
      stopStateSync()
      singleton?.close()
      singleton = null
      ownsConnection = false
      wsStore.setState('closed')
    },
  }
}
