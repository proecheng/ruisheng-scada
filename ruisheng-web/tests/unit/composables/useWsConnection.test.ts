import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useWsConnection } from '@/composables/useWsConnection'

let connectError: Error | null = null
let closeConnection: (() => void) | undefined

const ConnectionHarness = defineComponent({
  setup() {
    closeConnection = useWsConnection().close
    return () => null
  },
})

vi.mock('@/ws/client', () => {
  class MockWSClient {
    private closed = false

    get state() {
      if (this.closed) throw new Error('closed client state was accessed')
      return 'open' as const
    }

    on() {
      return () => undefined
    }

    connect() {
      if (connectError) throw connectError
    }

    send() {}

    close() {
      this.closed = true
    }
  }

  return { WSClient: MockWSClient }
})

describe('useWsConnection', () => {
  beforeEach(() => {
    connectError = null
    closeConnection = undefined
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('stops state synchronization when explicitly closed', () => {
    const wrapper = mount(ConnectionHarness)

    expect(vi.getTimerCount()).toBe(1)
    closeConnection?.()
    expect(vi.getTimerCount()).toBe(0)
    expect(() => vi.advanceTimersByTime(500)).not.toThrow()

    wrapper.unmount()
  })

  it('does not retain a client or timer when connect throws synchronously', () => {
    connectError = new Error('WebSocket constructor failed')
    const wrapper = mount(ConnectionHarness)

    expect(vi.getTimerCount()).toBe(0)
    expect(() => closeConnection?.()).not.toThrow()
    wrapper.unmount()
  })

  it('closes the owned connection when its layout unmounts', () => {
    const wrapper = mount(ConnectionHarness)

    expect(vi.getTimerCount()).toBe(1)
    wrapper.unmount()
    expect(vi.getTimerCount()).toBe(0)
    expect(() => vi.advanceTimersByTime(500)).not.toThrow()
  })
})
