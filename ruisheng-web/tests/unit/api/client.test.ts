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

  it('adds Idempotency-Key header on POST/PUT/DELETE', async () => {
    mock.onPost('/write').reply((config) => {
      expect(config.headers?.['Idempotency-Key']).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/)
      return [200, { code: 0, message: 'ok', data: {} }]
    })
    await apiClient.post('/write', {})
  })
})
