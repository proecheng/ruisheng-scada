import { describe, it, expect, beforeEach, vi } from 'vitest'
import MockAdapter from 'axios-mock-adapter'
import { apiClient, setAuthToken } from '@/api/client'

describe('apiClient', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = new MockAdapter(apiClient)
    setAuthToken(null)
  })

  it('injects X-Trace-Id header on every request', async () => {
    mock.onGet('/test').reply((config) => {
      expect(config.headers?.['X-Trace-Id']).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/)
      return [200, { code: 0, message: 'ok', data: {} }]
    })
    await apiClient.get('/test')
  })

  it('injects Authorization header when token is set', async () => {
    setAuthToken('fake-jwt')
    mock.onGet('/secure').reply((config) => {
      expect(config.headers?.Authorization).toBe('Bearer fake-jwt')
      return [200, { code: 0, message: 'ok', data: {} }]
    })
    await apiClient.get('/secure')
  })

  it('unwraps ApiResponse.data on success', async () => {
    mock.onGet('/wrap').reply(200, { code: 0, message: 'ok', data: { foo: 'bar' } })
    const res = await apiClient.get('/wrap')
    expect(res.data).toEqual({ code: 0, message: 'ok', data: { foo: 'bar' } })
  })

  it('throws on non-zero code with mapped message', async () => {
    mock.onGet('/fail').reply(200, { code: -200, message: 'offline', trace_id: 't-1' })
    await expect(apiClient.get('/fail')).rejects.toThrow(/设备离线|offline/)
  })

  it('preserves the live backend msg/transid error shape', async () => {
    mock.onGet('/backend-shape').reply(400, {
      code: -999,
      msg: 'backend detail',
      transid: 'tx-live-1',
      data: null,
    })

    await expect(apiClient.get('/backend-shape')).rejects.toMatchObject({
      code: -999,
      message: 'backend detail',
      traceId: 'tx-live-1',
    })
  })

  it.each([
    '/auth/login',
    'auth/login/',
    'https://api.example.test/api/auth/login/?source=absolute',
  ])('classifies login 401/-101 for normalized URL %s without expiring the session', async (url) => {
    const onAuthExpired = vi.fn()
    window.addEventListener('ruisheng:auth-expired', onAuthExpired)
    mock.onPost(url).reply(401, { code: -101, message: 'invalid credentials', data: null })

    try {
      await expect(apiClient.post(url, {})).rejects.toMatchObject({
        code: -101,
        message: '用户名或密码错误',
      })
      expect(onAuthExpired).not.toHaveBeenCalled()
    } finally {
      window.removeEventListener('ruisheng:auth-expired', onAuthExpired)
    }
  })

  it('returns the same coded credentials error for a login 401 without a structured body', async () => {
    const onAuthExpired = vi.fn()
    window.addEventListener('ruisheng:auth-expired', onAuthExpired)
    mock.onPost('/auth/login/').reply(401)

    try {
      await expect(apiClient.post('/auth/login/', {})).rejects.toMatchObject({
        code: -101,
        message: '用户名或密码错误',
      })
      expect(onAuthExpired).not.toHaveBeenCalled()
    } finally {
      window.removeEventListener('ruisheng:auth-expired', onAuthExpired)
    }
  })

  it('uses the explicit auth marker when an intermediary rewrites the login URL', async () => {
    const onAuthExpired = vi.fn()
    window.addEventListener('ruisheng:auth-expired', onAuthExpired)
    mock.onPost('/rewritten/login-endpoint').reply(401, {
      code: -101,
      msg: 'invalid credentials',
      transid: 'tx-login-1',
      data: null,
    })

    try {
      await expect(
        apiClient.post('/rewritten/login-endpoint', {}, { ruishengAuthRequest: true }),
      ).rejects.toMatchObject({
        code: -101,
        message: '用户名或密码错误',
        traceId: 'tx-login-1',
      })
      expect(onAuthExpired).not.toHaveBeenCalled()
    } finally {
      window.removeEventListener('ruisheng:auth-expired', onAuthExpired)
    }
  })

  it('still expires the session for a protected API 401/-101', async () => {
    const onAuthExpired = vi.fn()
    window.addEventListener('ruisheng:auth-expired', onAuthExpired)
    mock.onGet('/secure').reply(401, { code: -101, message: 'token expired', data: null })

    try {
      await expect(apiClient.get('/secure')).rejects.toMatchObject({
        code: -101,
        message: '登录已过期',
      })
      expect(onAuthExpired).toHaveBeenCalledTimes(1)
    } finally {
      window.removeEventListener('ruisheng:auth-expired', onAuthExpired)
    }
  })

  it('adds Idempotency-Key header on POST/PUT/DELETE', async () => {
    mock.onPost('/write').reply((config) => {
      expect(config.headers?.['Idempotency-Key']).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/)
      return [200, { code: 0, message: 'ok', data: {} }]
    })
    await apiClient.post('/write', {})
  })
})
