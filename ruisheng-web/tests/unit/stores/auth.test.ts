import { describe, it, expect, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'

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
    localStorage.setItem('access_token', 'persisted')
    localStorage.setItem(
      'user',
      JSON.stringify({ user_name: 'x', authority: 'User', usr_group: 'g' }),
    )
    const s = useAuthStore()
    s.hydrate()
    expect(s.isAuthenticated).toBe(true)
    expect(s.user?.user_name).toBe('x')
  })
})
