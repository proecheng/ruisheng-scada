import axios, { AxiosError, type AxiosInstance, type InternalAxiosRequestConfig } from 'axios'
import { generateUlid } from '@/utils/ulid'
import { mapErrCode } from '@/utils/errors'
import type { ApiResponse } from '@/api/types'

const BASE = import.meta.env.VITE_API_BASE ?? '/api'

let authToken: string | null = null
export function setAuthToken(token: string | null): void {
  authToken = token
}
export function getAuthToken(): string | null {
  return authToken
}

export const apiClient: AxiosInstance = axios.create({
  baseURL: BASE,
  timeout: 30000,
})

apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const traceId = generateUlid()
  config.headers.set('X-Trace-Id', traceId)
  const method = (config.method ?? 'get').toUpperCase()
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    if (!config.headers.get('Idempotency-Key')) {
      config.headers.set('Idempotency-Key', generateUlid())
    }
  }
  if (authToken) config.headers.set('Authorization', `Bearer ${authToken}`)
  return config
})

apiClient.interceptors.response.use(
  (response) => {
    const body = response.data as ApiResponse
    if (body && typeof body === 'object' && 'code' in body && body.code !== 0) {
      const err = mapErrCode(body.code, body.message)
      const e = new Error(err.headline) as Error & { code: number; hint?: string; traceId?: string }
      e.code = body.code
      e.hint = err.hint
      e.traceId = body.trace_id
      throw e
    }
    return response
  },
  (error: AxiosError<ApiResponse>) => {
    const body = error.response?.data
    if (body && typeof body === 'object' && 'code' in body) {
      const err = mapErrCode(body.code, body.message)
      const e = new Error(err.headline) as Error & { code: number; hint?: string; traceId?: string }
      e.code = body.code
      e.hint = err.hint
      e.traceId = body.trace_id
      return Promise.reject(e)
    }
    return Promise.reject(error)
  },
)
