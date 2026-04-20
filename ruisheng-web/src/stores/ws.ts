import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { WSMessage, WSState } from '@/ws/types'

export const useWsStore = defineStore('ws', () => {
  const state = ref<WSState>('closed')
  const lastMessage = ref<WSMessage | null>(null)
  const messageCount = ref(0)

  const isHealthy = computed(() => state.value === 'open')

  function setState(s: WSState): void {
    state.value = s
  }

  function pushMessage(m: WSMessage): void {
    lastMessage.value = m
    messageCount.value++
  }

  return { state, lastMessage, messageCount, isHealthy, setState, pushMessage }
})
