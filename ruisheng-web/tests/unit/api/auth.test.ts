import { describe, it, expect, beforeEach } from 'vitest'
import MockAdapter from 'axios-mock-adapter'
import { apiClient } from '@/api/client'
import { login, logout, refresh, smsSend } from '@/api/auth'

describe('auth api', () => {
  let mock: MockAdapter
  beforeEach(() => {
    mock = new MockAdapter(apiClient)
  })

  it('login posts credentials and returns session', async () => {
    mock.onPost('/auth/login').reply(200, {
      code: 0,
      message: 'ok',
      data: {
        access_token: 'j1',
        refresh_token: 'r1',
        user: { user_name: 'u', authority: 'User', usr_group: 'g' },
      },
    })
    const s = await login({ user_name: 'u', password: 'p' })
    expect(s.access_token).toBe('j1')
  })

  it('refresh posts refresh_token and returns new session', async () => {
    mock.onPost('/auth/refresh').reply(200, {
      code: 0,
      message: 'ok',
      data: {
        access_token: 'j2',
        refresh_token: 'r2',
        user: { user_name: 'u', authority: 'User', usr_group: 'g' },
      },
    })
    const s = await refresh('r1')
    expect(s.access_token).toBe('j2')
  })

  it('logout succeeds', async () => {
    mock.onPost('/auth/logout').reply(200, { code: 0, message: 'ok' })
    await expect(logout()).resolves.toBeUndefined()
  })

  it('smsSend posts phone', async () => {
    mock.onPost('/auth/sms/send').reply(200, { code: 0, message: 'ok' })
    await expect(smsSend({ phone: '13800000000', scene: 'login' })).resolves.toBeUndefined()
  })
})
