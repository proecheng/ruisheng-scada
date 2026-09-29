import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import MockAdapter from 'axios-mock-adapter'
import { apiClient, setAuthToken } from '@/api/client'
import { installSessionRenewal } from '@/auth/sessionRenewal'
import { useAuthStore, type Session } from '@/stores/auth'

const user = { user_name: 'alice', authority: 'User' as const, usr_group: 'g', control_authority: 0 }
let startedAt: number
function session(id: string, expiresIn = 900, deadline = startedAt + 86400): Session {
  const encode = (value: object) => btoa(JSON.stringify(value)).split('=').join('').split('+').join('-').split('/').join('_')
  return {
    access_token: [encode({ alg: 'HS256' }), encode({
      sub: user.user_name, role: user.authority, usr_group: user.usr_group, ca: 0,
      typ: 'access', iat: startedAt, exp: Math.floor(Date.now() / 1000) + expiresIn,
      session_exp: deadline, jti: id,
    }), 'signature'].join('.'),
    refresh_token: 'refresh-' + id,
    user,
  }
}

describe('automatic session renewal', () => {
  let mock: MockAdapter
  let auth: ReturnType<typeof useAuthStore>
  let dispose: (() => void) | undefined
  let expired: () => void
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-15T05:00:00Z'))
    startedAt = Math.floor(Date.now() / 1000)
    setActivePinia(createPinia())
    localStorage.clear()
    setAuthToken(null)
    auth = useAuthStore()
    mock = new MockAdapter(apiClient)
    expired = vi.fn(() => auth.logout())
    window.addEventListener('ruisheng:auth-expired', expired)
  })
  afterEach(() => {
    dispose?.()
    window.removeEventListener('ruisheng:auth-expired', expired)
    mock.restore()
    vi.useRealTimers()
  })
  function start(value = session('initial')) {
    auth.setSession(value)
    dispose = installSessionRenewal(auth)
  }

  it('renews before 15 minutes even without page requests', async () => {
    start()
    mock.onPost('/auth/refresh').reply(() => [200, { code: 0, data: session('renewed') }])
    await vi.advanceTimersByTimeAsync(14 * 60 * 1000)
    expect(mock.history.post).toHaveLength(1)
    expect(auth.refreshToken).toBe('refresh-renewed')
    expect(expired).not.toHaveBeenCalled()
  })

  it('coalesces simultaneous expired responses and preserves write idempotency keys', async () => {
    const original = session('initial')
    start(original)
    mock.onPost('/auth/refresh').reply(() => [200, { code: 0, data: session('renewed') }])
    const keys: string[] = []
    mock.onPut('/points').reply(config => {
      keys.push(String(config.headers?.['Idempotency-Key']))
      return config.headers?.Authorization === 'Bearer ' + original.access_token
        ? [401, { code: -101, msg: 'expired' }]
        : [200, { code: 0, data: {} }]
    })
    await Promise.all([apiClient.put('/points', {}, { headers: { 'Idempotency-Key': 'write-a' } }),
      apiClient.put('/points', {}, { headers: { 'Idempotency-Key': 'write-b' } })])
    expect(mock.history.post).toHaveLength(1)
    expect(keys.filter(k => k === 'write-a')).toHaveLength(2)
    expect(keys.filter(k => k === 'write-b')).toHaveLength(2)
    expect(expired).not.toHaveBeenCalled()
  })

  it('recovers an expired access token loaded from browser storage', async () => {
    start(session('old', -5))
    mock.onPost('/auth/refresh').reply(200, { code: 0, data: session('renewed') })
    mock.onGet('/live').reply(200, { code: 0, data: {} })
    await apiClient.get('/live')
    expect(auth.refreshToken).toBe('refresh-renewed')
    expect(mock.history.post).toHaveLength(1)
    expect(mock.history.get).toHaveLength(1)
  })

  it('keeps the login during a temporary refresh outage and retries after recovery', async () => {
    start(session('old', 30))
    mock.onPost('/auth/refresh').replyOnce(503, { code: -999, msg: 'temporarily unavailable' })
    mock.onGet('/live').reply(200, { code: 0, data: {} })
    await apiClient.get('/live')
    expect(auth.isAuthenticated).toBe(true)
    expect(expired).not.toHaveBeenCalled()
    mock.onPost('/auth/refresh').reply(200, { code: 0, data: session('renewed') })
    await apiClient.get('/live')
    expect(auth.refreshToken).toBe('refresh-renewed')
  })

  it('returns to login when the refresh credential is rejected', async () => {
    start(session('old', -1))
    mock.onPost('/auth/refresh').reply(401, { code: -101, msg: 'revoked' })
    await expect(apiClient.get('/live')).rejects.toThrow()
    expect(expired).toHaveBeenCalledTimes(1)
    expect(auth.isAuthenticated).toBe(false)
    expect(mock.history.get).toHaveLength(0)
  })

  it('requires manual login at 24 hours without sending another refresh', async () => {
    start()
    vi.setSystemTime((startedAt + 86400) * 1000)
    await expect(apiClient.get('/live')).rejects.toThrow('24小时')
    expect(auth.isAuthenticated).toBe(false)
    expect(mock.history.post).toHaveLength(0)
    expect(mock.history.get).toHaveLength(0)
  })

  it('never restores a session after logout while refresh is in flight', async () => {
    start(session('old', -1))
    let release!: (value: [number, object]) => void
    let didStart!: () => void
    const begun = new Promise<void>(resolve => { didStart = resolve })
    mock.onPost('/auth/refresh').reply(() => new Promise(resolve => { release = resolve; didStart() }))
    const request = apiClient.get('/live').catch(error => error)
    await begun
    auth.logout()
    release([200, { code: 0, data: session('too-late') }])
    await request
    expect(auth.isAuthenticated).toBe(false)
    expect(localStorage.getItem('refresh_token')).toBeNull()
    expect(mock.history.get).toHaveLength(0)
  })

  it('uses renewal from another tab and follows its logout', async () => {
    start()
    const updated = session('other-tab')
    localStorage.setItem('user', JSON.stringify(updated.user))
    localStorage.setItem('access_token', updated.access_token)
    localStorage.setItem('refresh_token', updated.refresh_token)
    window.dispatchEvent(new StorageEvent('storage', { key: 'refresh_token' }))
    expect(auth.refreshToken).toBe(updated.refresh_token)
    mock.onGet('/live').reply(200, { code: 0, data: {} })
    await apiClient.get('/live')
    expect(mock.history.post).toHaveLength(0)
    localStorage.clear()
    window.dispatchEvent(new StorageEvent('storage', { key: null }))
    expect(auth.isAuthenticated).toBe(false)
  })
})
