import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'

function accessToken(claims: Record<string, unknown>): string {
  const encode = (value: Record<string, unknown>) =>
    btoa(JSON.stringify(value)).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_')
  return `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode(claims)}.signature`
}

const persistedUser = { user_name: 'x', authority: 'User' as const, usr_group: 'g' }
const persistedToken = accessToken({
  sub: persistedUser.user_name,
  role: persistedUser.authority,
  usr_group: persistedUser.usr_group,
  ca: 0,
  typ: 'access',
})

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('starts unauthenticated', () => {
    const s = useAuthStore()
    expect(s.isAuthenticated).toBe(false)
    expect(s.user).toBeNull()
  })

  it('setSession stores token + user and marks authenticated', () => {
    const s = useAuthStore()
    s.setSession({
      access_token: 'j1',
      refresh_token: 'r1',
      user: { user_name: 'admin', authority: 'Administrators', usr_group: 'root' },
    })
    expect(s.isAuthenticated).toBe(true)
    expect(s.user?.user_name).toBe('admin')
    expect(localStorage.getItem('access_token')).toBe('j1')
  })

  it('logout clears session + storage', () => {
    const s = useAuthStore()
    s.setSession({
      access_token: 'j1',
      refresh_token: 'r1',
      user: { user_name: 'u', authority: 'User', usr_group: 'g' },
    })
    s.logout()
    expect(s.isAuthenticated).toBe(false)
    expect(localStorage.getItem('access_token')).toBeNull()
  })

  it('hasRole returns true when authority matches any', () => {
    const s = useAuthStore()
    s.setSession({
      access_token: 'j1',
      refresh_token: 'r1',
      user: { user_name: 'u', authority: 'Company', usr_group: 'g' },
    })
    expect(s.hasRole(['Company', 'GroupCompany'])).toBe(true)
    expect(s.hasRole(['Administrators'])).toBe(false)
  })

  it('hydrates from localStorage on init', () => {
    localStorage.setItem('access_token', persistedToken)
    localStorage.setItem('user', JSON.stringify(persistedUser))
    const s = useAuthStore()
    s.hydrate()
    expect(s.isAuthenticated).toBe(true)
    expect(s.user?.user_name).toBe('x')
  })

  it('clears a corrupted persisted session without throwing', () => {
    localStorage.setItem('access_token', 'persisted')
    localStorage.setItem('refresh_token', 'refresh')
    localStorage.setItem('user', '{not-json')
    const s = useAuthStore()

    expect(() => s.hydrate()).not.toThrow()
    expect(s.isAuthenticated).toBe(false)
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
    expect(localStorage.getItem('user')).toBeNull()
  })

  it('rejects a forged persisted user shape', () => {
    localStorage.setItem('access_token', persistedToken)
    localStorage.setItem(
      'user',
      JSON.stringify({ user_name: 'mallory', authority: 'Root', usr_group: 'tenant-a' }),
    )
    const s = useAuthStore()

    s.hydrate()

    expect(s.isAuthenticated).toBe(false)
    expect(s.user).toBeNull()
    expect(localStorage.getItem('access_token')).toBeNull()
  })

  it('rejects persisted user privileges that disagree with access token claims', () => {
    localStorage.setItem('access_token', persistedToken)
    localStorage.setItem(
      'user',
      JSON.stringify({ ...persistedUser, authority: 'Administrators' }),
    )
    const s = useAuthStore()

    s.hydrate()

    expect(s.isAuthenticated).toBe(false)
    expect(s.user).toBeNull()
  })

  it('clears in-memory state when browser storage is unavailable', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError')
    })
    const s = useAuthStore()

    expect(() => s.hydrate()).not.toThrow()
    expect(s.isAuthenticated).toBe(false)

    getItem.mockRestore()
  })
})
