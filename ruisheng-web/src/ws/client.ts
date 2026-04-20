import type { WSListener, WSMessage, WSState } from '@/ws/types'

export interface WSClientOptions {
  initialBackoffMs?: number
  maxBackoffMs?: number
  heartbeatMs?: number
}

export class WSClient {
  private ws: WebSocket | null = null
  private listeners = new Set<WSListener>()
  private closedByUser = false
  private backoffMs: number
  private readonly initialBackoffMs: number
  private readonly maxBackoffMs: number
  private readonly heartbeatMs: number
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null
  public state: WSState = 'closed'

  constructor(
    private readonly url: string,
    opts: WSClientOptions = {},
  ) {
    this.initialBackoffMs = opts.initialBackoffMs ?? 1000
    this.maxBackoffMs = opts.maxBackoffMs ?? 30000
    this.heartbeatMs = opts.heartbeatMs ?? 30000
    this.backoffMs = this.initialBackoffMs
  }

  on(listener: WSListener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  connect(): void {
    this.closedByUser = false
    this.openSocket()
  }

  close(): void {
    this.closedByUser = true
    this.state = 'closed'
    this.stopHeartbeat()
    this.ws?.close()
    this.ws = null
  }

  send(msg: unknown): void {
    if (this.ws?.readyState === 1) {
      this.ws.send(JSON.stringify(msg))
    }
  }

  private openSocket(): void {
    this.state = 'connecting'
    this.ws = new WebSocket(this.url)
    this.ws.onopen = () => {
      this.state = 'open'
      this.backoffMs = this.initialBackoffMs
      this.startHeartbeat()
    }
    this.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as WSMessage
        this.listeners.forEach((l) => l(msg))
      } catch {
        // ignore malformed
      }
    }
    this.ws.onclose = () => {
      this.stopHeartbeat()
      if (this.closedByUser) return
      this.state = 'reconnecting'
      const delay = this.backoffMs
      this.backoffMs = Math.min(this.backoffMs * 2, this.maxBackoffMs)
      setTimeout(() => {
        if (!this.closedByUser) this.openSocket()
      }, delay)
    }
    this.ws.onerror = () => {
      /* let onclose handle reconnect */
    }
  }

  private startHeartbeat(): void {
    this.stopHeartbeat()
    this.heartbeatTimer = setInterval(() => {
      this.send({ type: 'ping', ts: new Date().toISOString() })
    }, this.heartbeatMs)
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }
}
