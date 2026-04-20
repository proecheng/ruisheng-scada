import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface DiagEntry {
  at: string
  kind: 'api' | 'ws' | 'error'
  label: string
  detail?: string
  traceId?: string
  durationMs?: number
}

export const useDiagStore = defineStore('diag', () => {
  const entries = ref<DiagEntry[]>([])

  function record(e: DiagEntry): void {
    entries.value.unshift(e)
    if (entries.value.length > 50) entries.value.length = 50
  }
  function clear(): void {
    entries.value = []
  }

  return { entries, record, clear }
})
