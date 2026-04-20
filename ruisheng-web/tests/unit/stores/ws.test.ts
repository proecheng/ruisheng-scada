import { describe, it, expect, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useWsStore } from '@/stores/ws'

describe('useWsStore', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('starts with closed state and empty last message', () => {
    const s = useWsStore()
    expect(s.state).toBe('closed')
    expect(s.lastMessage).toBeNull()
  })

  it('setState updates state reactively', () => {
    const s = useWsStore()
    s.setState('open')
    expect(s.state).toBe('open')
  })

  it('pushMessage sets lastMessage and increments counter', () => {
    const s = useWsStore()
    s.pushMessage({ type: 'ping', ts: '2026-01-01T00:00:00Z' })
    expect(s.lastMessage?.type).toBe('ping')
    expect(s.messageCount).toBe(1)
  })

  it('isHealthy is true only when state=open', () => {
    const s = useWsStore()
    expect(s.isHealthy).toBe(false)
    s.setState('open')
    expect(s.isHealthy).toBe(true)
    s.setState('reconnecting')
    expect(s.isHealthy).toBe(false)
  })
})
