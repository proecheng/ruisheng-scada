import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export interface AlarmLite {
  event_id: number
  dev_number: string
  alarm_name: string
  value: number
  limit: number
  ts: string
  acked: boolean
}

export const useAlarmsStore = defineStore('alarms', () => {
  const feed = ref<AlarmLite[]>([])

  const unackedCount = computed(() => feed.value.filter((a) => !a.acked).length)

  function push(a: AlarmLite): void {
    feed.value.unshift(a)
    if (feed.value.length > 500) feed.value.length = 500
  }
  function ack(event_id: number): void {
    const a = feed.value.find((x) => x.event_id === event_id)
    if (a) a.acked = true
  }
  function clear(): void {
    feed.value = []
  }

  return { feed, unackedCount, push, ack, clear }
})
