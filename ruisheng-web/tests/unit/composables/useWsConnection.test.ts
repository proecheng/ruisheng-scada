import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useWsConnection } from '@/composables/useWsConnection'
import { useAuthStore } from '@/stores/auth'
import { nextTick } from 'vue'

let connectError: Error | null = null
let closeConnection: (() => void) | undefined
const connectedUrls: string[] = []

const ConnectionHarness = defineComponent({
  setup() {
    closeConnection = useWsConnection().close
    return () => null
  },
})

vi.mock('@/ws/client', () => {
  class MockWSClient {
    private closed = false
    constructor(url: string) { connectedUrls.push(url) }

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
    connectedUrls.length = 0
    useAuthStore().setSession({ access_token: 'initial-token', refresh_token: 'refresh', user: {
      user_name: 'test', authority: 'User', usr_group: 'g',
    } })
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

  it('replaces the socket after renewal and closes it without reconnecting on logout', async () => {
    const wrapper = mount(ConnectionHarness)
    const auth = useAuthStore()
    auth.setSession({ access_token: 'renewed-token', refresh_token: 'renewed-refresh', user: {
      user_name: 'test', authority: 'User', usr_group: 'g',
    } })
    await nextTick()
    await vi.advanceTimersByTimeAsync(0)
    expect(connectedUrls).toHaveLength(2)
    expect(connectedUrls[1]).toContain('token=renewed-token')
    expect(vi.getTimerCount()).toBe(1)
    auth.logout()
    await nextTick()
    await vi.advanceTimersByTimeAsync(0)
    expect(connectedUrls).toHaveLength(2)
    expect(vi.getTimerCount()).toBe(0)
    wrapper.unmount()
  })
})
