import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { WSClient } from '@/ws/client'
import type { WSMessage } from '@/ws/types'

class MockWebSocket {
  static instances: MockWebSocket[] = []
  readyState = 0
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  sent: string[] = []
  constructor(public url: string) {
    MockWebSocket.instances.push(this)
    setTimeout(() => {
      this.readyState = 1
      this.onopen?.(new Event('open'))
    }, 0)
  }
  send(data: string): void {
    this.sent.push(data)
  }
  close(): void {
    this.readyState = 3
    this.onclose?.(new CloseEvent('close'))
  }
  emit(msg: WSMessage): void {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(msg) }))
  }
}

describe('WSClient', () => {
  let origWS: typeof WebSocket
  beforeEach(() => {
    origWS = globalThis.WebSocket
    // @ts-expect-error mock
    globalThis.WebSocket = MockWebSocket
    MockWebSocket.instances = []
    vi.useFakeTimers()
  })
  afterEach(() => {
    globalThis.WebSocket = origWS
    vi.useRealTimers()
  })

  it('connects and transitions state to open', async () => {
    const client = new WSClient('ws://test/ws')
    client.connect()
    await vi.runOnlyPendingTimersAsync()
    expect(client.state).toBe('open')
  })

  it('dispatches parsed messages to listeners', async () => {
    const client = new WSClient('ws://test/ws')
    const received: WSMessage[] = []
    client.on((m) => received.push(m))
    client.connect()
    await vi.runOnlyPendingTimersAsync()
    MockWebSocket.instances[0]!.emit({
      type: 'realtime',
      dev_number: 'D1',
      point_id: 1,
      value: 42,
      ts: '2026-04-20T00:00:00Z',
    })
    expect(received).toHaveLength(1)
    expect(received[0]).toMatchObject({ type: 'realtime', dev_number: 'D1' })
  })

  it('auto-reconnects after unexpected close with exponential backoff', async () => {
    const client = new WSClient('ws://test/ws', { initialBackoffMs: 100, maxBackoffMs: 1000 })
    client.connect()
    await vi.runOnlyPendingTimersAsync()
    MockWebSocket.instances[0]!.close()
    expect(client.state).toBe('reconnecting')
    await vi.advanceTimersByTimeAsync(100)
    await vi.runOnlyPendingTimersAsync()
    expect(MockWebSocket.instances.length).toBe(2)
    expect(client.state).toBe('open')
  })

  it('does not reconnect after explicit close()', async () => {
    const client = new WSClient('ws://test/ws')
    client.connect()
    await vi.runOnlyPendingTimersAsync()
    client.close()
    expect(client.state).toBe('closed')
    await vi.advanceTimersByTimeAsync(5000)
    expect(MockWebSocket.instances.length).toBe(1)
  })

  it('sends ping heartbeat every 30s', async () => {
    const client = new WSClient('ws://test/ws', { heartbeatMs: 30000 })
    client.connect()
    await vi.runOnlyPendingTimersAsync()
    await vi.advanceTimersByTimeAsync(30000)
    const inst = MockWebSocket.instances[0]!
    expect(inst.sent.some((s) => s.includes('"ping"'))).toBe(true)
  })
})
