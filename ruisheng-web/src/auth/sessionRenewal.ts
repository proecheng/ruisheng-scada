import { watch } from 'vue'
import { refresh } from '@/api/auth'
import { setSessionRenewal } from '@/api/client'
import { decodeAccessClaims, type useAuthStore } from '@/stores/auth'

type AuthStore = ReturnType<typeof useAuthStore>
const EARLY_REFRESH_MS = 60_000

function deadlines(token: string | null) {
  const claims = token ? decodeAccessClaims(token) : null
  const expires = typeof claims?.exp === 'number' ? claims.exp * 1000 : Infinity
  const session = typeof claims?.session_exp === 'number'
    ? claims.session_exp * 1000
    : typeof claims?.iat === 'number' ? (claims.iat + 86400) * 1000 : Infinity
  return { expires, session }
}

/** Renew only within the fixed login session; never turn a transport failure into logout. */
export function installSessionRenewal(auth: AuthStore): () => void {
  let pending: Promise<void> | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let stopped = false

  const expire = () => {
    window.dispatchEvent(new CustomEvent('ruisheng:auth-expired'))
  }
  const syncStorage = () => {
    try {
      if (localStorage.getItem('refresh_token') !== auth.refreshToken) auth.hydrate()
    } catch { /* In-memory sessions still work when storage is unavailable. */ }
  }
  const schedule = (retry = false) => {
    if (timer !== null) clearTimeout(timer)
    timer = null
    if (stopped || !auth.accessToken) return
    const { expires, session } = deadlines(auth.accessToken)
    const untilRefresh = expires - Date.now() - (expires >= session ? 0 : EARLY_REFRESH_MS)
    const next = Math.min(session - Date.now(), retry ? Math.max(15_000, untilRefresh) : untilRefresh)
    if (!Number.isFinite(next)) return
    timer = setTimeout(() => { void renew().catch(() => {}).finally(() => schedule(true)) }, Math.max(0, next))
  }
  const rotate = async (failedToken?: string) => {
    syncStorage()
    const token = auth.accessToken
    const refreshToken = auth.refreshToken
    if (!token) throw new Error('登录已结束')
    const { expires, session } = deadlines(token)
    if (Date.now() >= session) {
      expire()
      throw new Error('登录已满24小时，请重新登录')
    }
    const rejectedCurrent = failedToken !== undefined && failedToken === token
    if (!rejectedCurrent && (expires >= session || expires > Date.now() + EARLY_REFRESH_MS)) return
    if (!refreshToken) {
      expire()
      throw new Error('登录已过期')
    }
    try {
      const updated = await refresh(refreshToken)
      syncStorage()
      // A logout or a different login during the request must win.
      if (auth.refreshToken !== refreshToken || stopped) throw new Error('登录状态已改变')
      auth.setSession(updated)
    } catch (error) {
      syncStorage()
      if (auth.refreshToken !== refreshToken) {
        if (auth.accessToken && deadlines(auth.accessToken).expires > Date.now()) return
        throw error
      }
      const code = (error as { code?: number }).code
      const status = (error as { response?: { status?: number } }).response?.status
      if (code === -101 || status === 401) expire()
      // A proactive refresh failure can still use the unexpired access token.
      else if (!rejectedCurrent && expires > Date.now()) return
      throw error
    }
  }
  const renew = (failedToken?: string): Promise<void> => {
    if (pending) return pending
    const task = (async () => {
      if (navigator.locks) await navigator.locks.request('ruisheng-session-refresh', () => rotate(failedToken))
      else await rotate(failedToken)
    })()
    pending = task.finally(() => { pending = null })
    return pending
  }
  const wake = () => { void renew().catch(() => {}).finally(() => schedule()) }
  const storage = (event: StorageEvent) => {
    if (event.key !== 'refresh_token' && event.key !== null) return
    syncStorage()
    if (!auth.accessToken) expire()
    schedule()
  }
  const visibility = () => { if (document.visibilityState === 'visible') wake() }
  const unwatch = watch(() => auth.accessToken, () => schedule(), { flush: 'sync' })
  setSessionRenewal(renew)
  window.addEventListener('storage', storage)
  window.addEventListener('online', wake)
  window.addEventListener('focus', wake)
  document.addEventListener('visibilitychange', visibility)
  schedule()
  return () => {
    stopped = true
    if (timer !== null) clearTimeout(timer)
    unwatch()
    setSessionRenewal(null)
    window.removeEventListener('storage', storage)
    window.removeEventListener('online', wake)
    window.removeEventListener('focus', wake)
    document.removeEventListener('visibilitychange', visibility)
  }
}
